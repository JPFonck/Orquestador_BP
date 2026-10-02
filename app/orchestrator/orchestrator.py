"""Orquestador del onboarding digital.

Máquina de estados explícita que coordina a los agentes especializados:

    RECEIVED → IDENTITY_AND_RISK (en paralelo) → GUARDRAILS → DOCUMENTATION → DECISION → RESPONSE
             → COMPLETED | AWAITING_CUSTOMER | AWAITING_REVIEW

El estado se persiste tras cada paso para poder reanudar la sesión cuando el cliente aporta
evidencia (`handle_message`) o un revisor resuelve un escalamiento (`review`).
"""

import asyncio
import logging
from typing import Any, Callable, TypeVar

from app.agents.base import AgentRun, BaseAgent
from app.agents.decision_agent import DecisionAgent
from app.agents.documentation_agent import DocumentationAgent
from app.agents.identity_agent import IdentityAgent
from app.agents.outputs import DecisionOutput
from app.agents.response_agent import ResponseAgent, leaks_internal_info, render_template_message
from app.agents.risk_agent import RiskAgent
from app.core.config import Settings
from app.core.llm import LLMClient
from app.orchestrator import guardrails
from app.orchestrator.mitigation import BLOCKING, CATALOG, MitigationCode, resolve
from app.policies.loader import BankPolicies
from app.state.models import (
    ConversationTurn,
    DecisionRecord,
    DocumentationResult,
    GuardrailEvaluation,
    IdentityResult,
    OnboardingState,
    OnboardingStatus,
    Prospect,
    RiskResult,
    Step,
)
from app.state.repository import StateRepository
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")

IDENTITY_MITIGATIONS = {
    MitigationCode.BIOMETRIC_REVERIFICATION.value,
    MitigationCode.VIDEO_CALL_VERIFICATION.value,
}


class SessionNotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    pass


class OnboardingOrchestrator:
    def __init__(
        self,
        llm: LLMClient,
        repository: StateRepository,
        policies: BankPolicies,
        settings: Settings,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._repo = repository
        self._policies = policies
        self._settings = settings
        registry = registry or ToolRegistry(policies)
        self.identity_agent = IdentityAgent(llm, registry, settings)
        self.risk_agent = RiskAgent(llm, registry, settings)
        self.documentation_agent = DocumentationAgent(llm, registry, settings)
        self.decision_agent = DecisionAgent(llm, registry, settings, effort=settings.decision_effort)
        self.response_agent = ResponseAgent(llm, registry, settings)

    # ------------------------------------------------------------------ API pública

    async def start(self, prospect: Prospect) -> OnboardingState:
        state = OnboardingState(prospect=prospect)
        state.add_event("onboarding_received", product=prospect.product)
        self._repo.save(state)
        return await self._run_pipeline(state, run_identity=True, run_risk=True)

    def get(self, session_id: str) -> OnboardingState:
        state = self._repo.get(session_id)
        if state is None:
            raise SessionNotFoundError(session_id)
        return state

    async def handle_message(
        self,
        session_id: str,
        message: str,
        liveness_token: str | None = None,
        product: str | None = None,
    ) -> OnboardingState:
        """Turno conversacional del cliente: aporta evidencia, corrige datos o pregunta."""
        state = self.get(session_id)
        state.conversation.append(
            ConversationTurn(
                role="customer",
                content=message,
                metadata={k: v for k, v in {"liveness_token": liveness_token, "product": product}.items() if v},
            )
        )
        state.add_event("customer_message", has_evidence=bool(liveness_token), product=product)

        if product and state.status == OnboardingStatus.NEEDS_INFO:
            state.prospect.product = product
            state.add_event("prospect_updated", product=product)
            return await self._run_pipeline(state, run_identity=True, run_risk=True)

        if liveness_token and self._awaiting_identity_evidence(state):
            state.evidence["liveness_token"] = liveness_token
            # La consulta de riesgo ya es válida: solo se repite la verificación de identidad.
            run_risk = state.risk_result is None or state.risk_result.status == "failed"
            return await self._run_pipeline(state, run_identity=True, run_risk=run_risk)

        # Sin acción que reanude el flujo: el agente de respuesta contesta con el estado actual.
        await self._respond(state)
        self._repo.save(state)
        return state

    async def review(
        self, session_id: str, approve: bool, reviewer: str, notes: str
    ) -> OnboardingState:
        """Resolución humana (analista / Oficial de Cumplimiento) de una solicitud escalada."""
        state = self.get(session_id)
        if state.status != OnboardingStatus.PENDING_REVIEW:
            raise InvalidTransitionError(
                f"La sesión está en estado {state.status}; solo se revisan solicitudes PENDING_REVIEW."
            )
        state.conversation.append(ConversationTurn(role="reviewer", content=notes, metadata={"reviewer": reviewer}))
        final = OnboardingStatus.APPROVED if approve else OnboardingStatus.REJECTED
        state.add_event("human_review", reviewer=reviewer, decision=final.value)

        if approve and (state.documentation_result is None or state.documentation_result.status == "failed"):
            await self._prepare_documentation(state)

        state.decision = DecisionRecord(decision=final, rationale=notes, decided_by="reviewer")
        state.status = final
        # Tras la aprobación solo quedan vigentes las mitigaciones no bloqueantes (p. ej. EDD).
        state.mitigations = [m for m in state.mitigations if m.code not in BLOCKING] if approve else []
        await self._respond(state)
        self._settle(state)
        self._repo.save(state)
        return state

    # ------------------------------------------------------------------ Pipeline

    async def _run_pipeline(
        self, state: OnboardingState, *, run_identity: bool, run_risk: bool
    ) -> OnboardingState:
        state.status = OnboardingStatus.IN_PROGRESS

        if not self._policies.is_supported(state.prospect.product):
            return await self._finish_unsupported_product(state)

        state.move_to(Step.IDENTITY_AND_RISK)
        self._repo.save(state)
        state.identity_result, state.risk_result = await asyncio.gather(
            self._verify_identity(state) if run_identity else _value(state.identity_result),
            self._check_risk(state) if run_risk else _value(state.risk_result),
        )
        self._repo.save(state)

        state.move_to(Step.GUARDRAILS)
        evaluation = guardrails.evaluate(state.identity_result, state.risk_result, self._policies)
        state.guardrails = evaluation
        state.add_event(
            "guardrails_evaluated",
            forced_decision=evaluation.forced_decision,
            required_mitigations=evaluation.required_mitigations,
            reasons=evaluation.reasons,
        )
        self._repo.save(state)

        if evaluation.forced_decision != OnboardingStatus.REJECTED:
            await self._prepare_documentation(state)

        state.move_to(Step.DECISION)
        await self._decide(state)
        self._repo.save(state)

        await self._respond(state)
        self._settle(state)
        self._repo.save(state)
        return state

    async def _verify_identity(self, state: OnboardingState) -> IdentityResult:
        payload = {
            "prospect": state.prospect.model_dump(),
            "evidence": state.evidence,
            "policy": {"identity_min_confidence": self._policies.thresholds.identity_min_confidence},
        }
        return await self._run_with_retry(
            self.identity_agent, payload, state,
            interpret=lambda run: self.identity_agent.interpret(run, self._policies),
            failed=lambda result: result.status == "failed",
        )

    async def _check_risk(self, state: OnboardingState) -> RiskResult:
        payload = {
            "prospect": {"name": state.prospect.prospect_name, "document_id": state.prospect.document_id}
        }
        return await self._run_with_retry(
            self.risk_agent, payload, state,
            interpret=self.risk_agent.interpret,
            failed=lambda result: result.status == "failed",
        )

    async def _prepare_documentation(self, state: OnboardingState) -> None:
        state.move_to(Step.DOCUMENTATION)
        risk = state.risk_result
        # Si el riesgo no se pudo determinar se asume "medium": exige la documentación de EDD.
        risk_level = risk.risk_level if risk and risk.risk_level else "medium"
        payload = {
            "product": state.prospect.product,
            "client_data": {
                "name": state.prospect.prospect_name,
                "document_id": state.prospect.document_id,
                "risk_level": risk_level,
            },
            "edd_required": bool(state.guardrails and state.guardrails.edd_required),
        }
        state.documentation_result = await self._run_with_retry(
            self.documentation_agent, payload, state,
            interpret=self.documentation_agent.interpret,
            failed=lambda result: result.status == "failed",
        )
        self._repo.save(state)

    async def _decide(self, state: OnboardingState) -> None:
        evaluation = state.guardrails
        assert evaluation is not None
        payload = {
            "prospect": state.prospect.model_dump(),
            "product_name": self._product_name(state),
            "identity": state.identity_result.model_dump() if state.identity_result else None,
            "risk": state.risk_result.model_dump() if state.risk_result else None,
            "documentation": state.documentation_result.model_dump() if state.documentation_result else None,
            "guardrails": evaluation.model_dump(mode="json"),
            "mitigation_catalog": [
                {"code": m.code, "title": m.title, "description": m.description} for m in CATALOG.values()
            ],
        }
        output: DecisionOutput | None = await self._run_with_retry(
            self.decision_agent, payload, state,
            interpret=lambda run: run.output,
            failed=lambda result: result is None,
        )

        proposed = OnboardingStatus(output.decision) if output else None
        final, overridden = guardrails.enforce(proposed, evaluation)
        if overridden:
            state.add_event("guardrail_override", proposed=proposed, final=final.value)

        codes = list(evaluation.required_mitigations)
        for code in output.mitigation_codes if output else []:
            if code.value in codes:
                continue
            # Una aprobación no puede llevar mitigaciones que la bloqueen.
            if final == OnboardingStatus.APPROVED and code in BLOCKING:
                continue
            codes.append(code.value)
        if final == OnboardingStatus.PENDING_REVIEW and not any(MitigationCode(c) in BLOCKING for c in codes):
            codes.append(MitigationCode.MANUAL_REVIEW.value)

        rationale = output.rationale if output else (
            "Decisión tomada por reglas de política (el agente de decisión no respondió): "
            + " ".join(evaluation.reasons or ["sin observaciones"])
        )
        state.decision = DecisionRecord(
            decision=final, rationale=rationale, llm_decision=proposed, overridden_by_guardrails=overridden
        )
        state.mitigations = resolve(codes)
        state.status = final
        state.add_event("decision_made", decision=final.value, mitigations=codes)

    async def _respond(self, state: OnboardingState) -> None:
        state.move_to(Step.RESPONSE)
        customer_turns = [t for t in state.conversation if t.role in ("customer", "assistant")]
        last_customer = next((t.content for t in reversed(customer_turns) if t.role == "customer"), None)
        payload: dict[str, Any] = {
            "customer_name": state.prospect.prospect_name,
            "product_name": self._product_name(state),
            "decision": state.status.value,
            "required_documents": [
                {"name": d.name, "description": d.description}
                for d in (state.documentation_result.required_documents if state.documentation_result else [])
            ],
            "customer_actions": [m.customer_action for m in state.mitigations if m.customer_action],
            "bank_follow_up": any(m.owner in ("bank", "compliance") for m in state.mitigations),
            "available_products": [p.display_name for p in self._policies.products.values()],
            "conversation": [{"role": t.role, "content": t.content} for t in customer_turns[-6:]],
            "customer_message": last_customer,
        }
        output = await self._run_with_retry(
            self.response_agent, payload, state,
            interpret=lambda run: run.output,
            failed=lambda result: result is None,
        )
        message = output.message if output else None
        if message is None:
            state.add_event("response_fallback", reason="agent_failed")
        elif leaks_internal_info(message):
            # Filtro de salida: la información de cumplimiento no puede llegar al cliente.
            state.add_event("response_fallback", reason="internal_info_leak")
            message = None
        state.customer_message = message or render_template_message(payload)
        state.conversation.append(ConversationTurn(role="assistant", content=state.customer_message))

    async def _finish_unsupported_product(self, state: OnboardingState) -> OnboardingState:
        state.move_to(Step.GUARDRAILS)
        reason = f"Producto '{state.prospect.product}' no disponible para onboarding digital."
        state.guardrails = GuardrailEvaluation(
            forced_decision=OnboardingStatus.NEEDS_INFO,
            allowed_decisions=[OnboardingStatus.NEEDS_INFO],
            required_mitigations=[MitigationCode.REQUEST_VALID_PRODUCT.value],
            reasons=[reason],
        )
        state.decision = DecisionRecord(decision=OnboardingStatus.NEEDS_INFO, rationale=reason)
        state.mitigations = resolve([MitigationCode.REQUEST_VALID_PRODUCT.value])
        state.status = OnboardingStatus.NEEDS_INFO
        state.add_event("decision_made", decision=state.status.value, reason=reason)
        await self._respond(state)
        self._settle(state)
        self._repo.save(state)
        return state

    # ------------------------------------------------------------------ Utilidades

    async def _run_with_retry(
        self,
        agent: BaseAgent[Any],
        payload: dict[str, Any],
        state: OnboardingState,
        *,
        interpret: Callable[[AgentRun[Any]], ResultT],
        failed: Callable[[ResultT], bool],
    ) -> ResultT:
        attempts = self._settings.max_agent_retries + 1
        for attempt in range(1, attempts + 1):
            run = await agent.run(payload, state)
            result = interpret(run)
            is_failed = failed(result)
            state.add_event(
                "agent_completed",
                agent=agent.name,
                attempt=attempt,
                failed=is_failed,
                status=getattr(result, "status", None),
                error=run.error,
                tool_errors=run.tool_errors,
                model=run.model,
            )
            if not is_failed:
                return result
            if run.error and not run.retryable and not run.tool_errors:
                break  # error definitivo (p. ej. rechazo del modelo o 4xx): reintentar no ayuda
            if attempt < attempts:
                state.add_event("agent_retry", agent=agent.name, next_attempt=attempt + 1)
                await asyncio.sleep(self._settings.retry_backoff_seconds * 2 ** (attempt - 1))
        logger.warning("Agente %s falló tras %d intento(s)", agent.name, attempt)
        return result

    def _settle(self, state: OnboardingState) -> None:
        if state.status in (OnboardingStatus.APPROVED, OnboardingStatus.REJECTED):
            step = Step.COMPLETED
        elif state.status == OnboardingStatus.NEEDS_INFO:
            step = Step.AWAITING_CUSTOMER
        elif any(m.owner in ("bank", "compliance") for m in state.mitigations):
            step = Step.AWAITING_REVIEW
        else:
            step = Step.AWAITING_CUSTOMER
        state.move_to(step)

    @staticmethod
    def _awaiting_identity_evidence(state: OnboardingState) -> bool:
        return state.status == OnboardingStatus.PENDING_REVIEW and any(
            m.code in IDENTITY_MITIGATIONS for m in state.mitigations
        )

    def _product_name(self, state: OnboardingState) -> str | None:
        policy = self._policies.product(state.prospect.product)
        return policy.display_name if policy else None


async def _value(value: ResultT) -> ResultT:
    return value

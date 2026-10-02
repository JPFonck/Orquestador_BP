import pytest

from app.core.llm import LLMError
from app.core.mock_llm import MockLLMClient, final, tool_use
from app.orchestrator.orchestrator import InvalidTransitionError
from app.state.models import OnboardingStatus, Step
from tests.conftest import prospect

S = OnboardingStatus


def codes(state):
    return [m.code for m in state.mitigations]


def events(state, type_):
    return [e for e in state.events if e.type == type_]


@pytest.mark.parametrize(
    ("name", "document_id", "product", "status", "step", "mitigations"),
    [
        ("Juan Perez", "1712345678", "cuenta_ahorros", S.APPROVED, Step.COMPLETED, []),
        ("Maria Lopez", "0923456789", "cuenta_ahorros", S.PENDING_REVIEW, Step.AWAITING_CUSTOMER,
         ["BIOMETRIC_REVERIFICATION"]),
        ("Carlos Ruiz", "1104567890", "cuenta_ahorros", S.PENDING_REVIEW, Step.AWAITING_CUSTOMER,
         ["VIDEO_CALL_VERIFICATION"]),
        ("Desconocido", "1708765432", "cuenta_ahorros", S.REJECTED, Step.COMPLETED, ["IN_BRANCH_VERIFICATION"]),
        ("Pedro Gomez", "1705555555", "tarjeta_credito", S.PENDING_REVIEW, Step.AWAITING_REVIEW,
         ["COMPLIANCE_ESCALATION"]),
        ("Ana Torres", "0102030405", "cuenta_corriente", S.APPROVED, Step.COMPLETED, ["ENHANCED_DUE_DILIGENCE"]),
        ("Tim Out", "9999999999", "cuenta_ahorros", S.PENDING_REVIEW, Step.AWAITING_REVIEW, ["MANUAL_REVIEW"]),
        ("Juan Perez", "1712345678", "hipoteca", S.NEEDS_INFO, Step.AWAITING_CUSTOMER, ["REQUEST_VALID_PRODUCT"]),
    ],
)
async def test_scenarios(orchestrator, name, document_id, product, status, step, mitigations):
    state = await orchestrator.start(prospect(name, document_id, product))
    assert state.status == status
    assert state.current_step == step
    assert codes(state) == mitigations
    assert state.customer_message
    assert state.decision.decision == status


async def test_happy_path_audit_and_documents(orchestrator, repository):
    state = await orchestrator.start(prospect())
    tools_used = {(c.agent, c.tool) for c in state.tool_calls}
    assert tools_used == {
        ("identity_agent", "verify_identity"),
        ("risk_agent", "check_risk_lists"),
        ("documentation_agent", "get_product_policy"),
        ("documentation_agent", "prepare_documentation"),
    }
    assert all(c.allowed for c in state.tool_calls)
    assert [d.code for d in state.documentation_result.required_documents] == [
        "CEDULA", "SERVICIO_BASICO", "CONTRATO_AHORROS"
    ]
    # El estado persistido es el mismo que se devolvió.
    assert repository.get(state.session_id).model_dump() == state.model_dump()


async def test_medium_risk_requests_edd_documents(orchestrator):
    state = await orchestrator.start(prospect("Ana Torres", "0102030405", "cuenta_corriente"))
    doc_codes = [d.code for d in state.documentation_result.required_documents]
    assert {"ORIGEN_FONDOS", "SUSTENTO_ACTIVIDAD"} <= set(doc_codes)


async def test_rejected_prospect_skips_documentation(orchestrator):
    state = await orchestrator.start(prospect("Desconocido", "1708765432"))
    assert state.documentation_result is None
    assert not any(c.agent == "documentation_agent" for c in state.tool_calls)


async def test_tool_timeout_is_retried_then_escalated(orchestrator, settings):
    state = await orchestrator.start(prospect("Tim Out", "9999999999"))
    identity_calls = [c for c in state.tool_calls if c.tool == "verify_identity"]
    assert len(identity_calls) == settings.max_agent_retries + 1
    assert len(events(state, "agent_retry")) == settings.max_agent_retries
    assert state.identity_result.status == "failed"


async def test_biometric_evidence_resumes_and_approves(orchestrator):
    state = await orchestrator.start(prospect("Maria Lopez", "0923456789"))
    risk_calls = sum(c.tool == "check_risk_lists" for c in state.tool_calls)

    state = await orchestrator.handle_message(
        state.session_id, "Ya completé la prueba de vida", liveness_token="LIVENESS-OK"
    )
    assert state.status == S.APPROVED
    assert state.identity_result.effective_confidence == 0.97
    # La consulta de riesgo no se repite: ya era concluyente.
    assert sum(c.tool == "check_risk_lists" for c in state.tool_calls) == risk_calls
    assert [t.role for t in state.conversation] == ["assistant", "customer", "assistant"]


async def test_failed_biometric_escalates_to_video_call(orchestrator):
    state = await orchestrator.start(prospect("Maria Lopez", "0923456789"))
    state = await orchestrator.handle_message(state.session_id, "Listo", liveness_token="LIVENESS-FAIL")
    assert state.status == S.PENDING_REVIEW
    assert codes(state) == ["VIDEO_CALL_VERIFICATION"]


async def test_message_without_evidence_only_replies(orchestrator):
    state = await orchestrator.start(prospect("Pedro Gomez", "1705555555", "tarjeta_credito"))
    calls_before = len(state.tool_calls)
    state = await orchestrator.handle_message(state.session_id, "¿Cuánto demora la revisión?")
    assert state.status == S.PENDING_REVIEW
    assert len(state.tool_calls) == calls_before
    assert state.conversation[-1].role == "assistant"


async def test_product_correction_restarts_flow(orchestrator):
    state = await orchestrator.start(prospect(product="hipoteca"))
    state = await orchestrator.handle_message(state.session_id, "Quiero una cuenta de ahorros", product="cuenta_ahorros")
    assert state.status == S.APPROVED
    assert state.prospect.product == "cuenta_ahorros"


async def test_compliance_review_approval(orchestrator):
    state = await orchestrator.start(prospect("Pedro Gomez", "1705555555", "tarjeta_credito"))
    state = await orchestrator.review(state.session_id, approve=True, reviewer="oficial.cumplimiento", notes="Homónimo descartado")
    assert state.status == S.APPROVED
    assert state.current_step == Step.COMPLETED
    assert state.decision.decided_by == "reviewer"
    assert codes(state) == []
    # Las notas internas del revisor no llegan al agente de respuesta ni al cliente.
    assert "Homónimo" not in state.customer_message


async def test_review_requires_pending_status(orchestrator):
    state = await orchestrator.start(prospect())
    with pytest.raises(InvalidTransitionError):
        await orchestrator.review(state.session_id, approve=False, reviewer="x", notes="no aplica")


# ------------------------------------------------------------------ Comportamientos anómalos del LLM


class GreedyIdentityLLM(MockLLMClient):
    """El agente de identidad intenta usar una tool que no le corresponde."""

    def _identity_agent(self, payload, results):
        if not results:
            return tool_use(
                [
                    ("verify_identity", {"document_id": payload["prospect"]["document_id"]}),
                    ("check_risk_lists", {"name": "x", "document_id": payload["prospect"]["document_id"]}),
                ],
                self._ids,
            )
        return super()._identity_agent(payload, results)


async def test_unauthorized_tool_request_is_blocked(make_orchestrator):
    state = await make_orchestrator(GreedyIdentityLLM()).start(prospect())
    blocked = [c for c in state.tool_calls if not c.allowed]
    assert [(c.agent, c.tool) for c in blocked] == [("identity_agent", "check_risk_lists")]
    assert events(state, "policy_violation")
    assert state.status == S.APPROVED  # la violación no altera el resultado legítimo


class RecklessDecisionLLM(MockLLMClient):
    """El agente de decisión intenta aprobar ignorando las reglas duras."""

    def _decision_agent(self, payload, results):
        return final({"decision": "APPROVED", "mitigation_codes": [], "rationale": "Todo correcto."})


async def test_guardrails_override_llm_decision(make_orchestrator):
    state = await make_orchestrator(RecklessDecisionLLM()).start(
        prospect("Pedro Gomez", "1705555555", "tarjeta_credito")
    )
    assert state.status == S.PENDING_REVIEW
    assert state.decision.llm_decision == S.APPROVED
    assert state.decision.overridden_by_guardrails is True
    assert codes(state) == ["COMPLIANCE_ESCALATION"]
    assert events(state, "guardrail_override")


class MisreportingIdentityLLM(MockLLMClient):
    """El agente reporta una confianza distinta a la que devolvió la tool."""

    def _identity_agent(self, payload, results):
        if not results:
            return super()._identity_agent(payload, results)
        return final({
            "status": "ok", "verified": True, "confidence": 0.99, "biometric_performed": False,
            "biometric_match": False, "biometric_confidence": 0.0, "reasoning": "Todo bien.",
        })


async def test_tool_observation_prevails_over_agent_report(make_orchestrator):
    state = await make_orchestrator(MisreportingIdentityLLM()).start(prospect("Maria Lopez", "0923456789"))
    assert state.identity_result.confidence == 0.72
    assert state.identity_result.discrepancies
    assert state.status == S.PENDING_REVIEW


class LeakyResponseLLM(MockLLMClient):
    def _response_agent(self, payload, results):
        return final({"message": "Tu solicitud está detenida porque apareces en la lista OFAC."})


async def test_response_leaking_internal_info_is_replaced(make_orchestrator):
    state = await make_orchestrator(LeakyResponseLLM()).start(prospect("Pedro Gomez", "1705555555", "tarjeta_credito"))
    assert "OFAC" not in state.customer_message
    assert events(state, "response_fallback")[0].detail["reason"] == "internal_info_leak"


class BrokenDecisionLLM(MockLLMClient):
    def _decision_agent(self, payload, results):
        raise LLMError("Error de la API (400): solicitud inválida", retryable=False)


async def test_decision_agent_failure_falls_back_to_policy(make_orchestrator):
    state = await make_orchestrator(BrokenDecisionLLM()).start(prospect())
    # Sin juicio del LLM la aprobación no es automática: pasa a revisión manual.
    assert state.status == S.PENDING_REVIEW
    assert codes(state) == ["MANUAL_REVIEW"]
    decision_attempts = [e for e in events(state, "agent_completed") if e.detail["agent"] == "decision_agent"]
    assert len(decision_attempts) == 1  # error definitivo: no se reintenta


class RefusingRiskLLM(MockLLMClient):
    def _risk_agent(self, payload, results):
        message = final({})
        message.stop_reason = "refusal"
        return message


async def test_refusal_is_handled_as_agent_failure(make_orchestrator):
    state = await make_orchestrator(RefusingRiskLLM()).start(prospect())
    assert state.risk_result.status == "failed"
    assert state.status == S.PENDING_REVIEW
    assert codes(state) == ["MANUAL_REVIEW"]

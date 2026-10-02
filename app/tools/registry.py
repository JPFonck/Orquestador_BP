"""Registro de tools con control de acceso por agente (allowlist) y auditoría de cada invocación."""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.schema import strict_json_schema
from app.policies.loader import BankPolicies
from app.state.models import ToolCallRecord
from app.tools import mocks


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyIdentityInput(_StrictInput):
    document_id: str


class VerifyBiometricsInput(_StrictInput):
    document_id: str
    liveness_token: str


class CheckRiskListsInput(_StrictInput):
    name: str
    document_id: str


class GetProductPolicyInput(_StrictInput):
    product: str


class ClientData(_StrictInput):
    name: str
    document_id: str
    risk_level: str


class PrepareDocumentationInput(_StrictInput):
    product: str
    client_data: ClientData


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]

    def definition(self) -> dict[str, Any]:
        """Definición para la Messages API con `strict: true` (el schema debe cerrar additionalProperties)."""
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": strict_json_schema(self.input_model),
        }


# Qué tools puede usar cada agente. Lo que no está aquí, no se puede invocar.
AGENT_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "identity_agent": frozenset({"verify_identity", "verify_biometrics"}),
    "risk_agent": frozenset({"check_risk_lists"}),
    "documentation_agent": frozenset({"get_product_policy", "prepare_documentation"}),
    "decision_agent": frozenset(),
    "response_agent": frozenset(),
}


@dataclass
class ToolExecution:
    result_block: dict[str, Any]
    record: ToolCallRecord

    @property
    def ok(self) -> bool:
        return self.record.allowed and self.record.error is None


class ToolRegistry:
    def __init__(
        self,
        policies: BankPolicies,
        allowlist: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._allowlist = allowlist or AGENT_TOOL_ALLOWLIST
        self._tools: dict[str, ToolSpec] = {
            spec.name: spec
            for spec in [
                ToolSpec(
                    "verify_identity",
                    "Verifica la identidad del prospecto contra el registro civil a partir de su "
                    "número de cédula. Devuelve {verified: bool, confidence: 0.0-1.0}.",
                    VerifyIdentityInput,
                    mocks.verify_identity,
                ),
                ToolSpec(
                    "verify_biometrics",
                    "Valida la prueba de vida (selfie + liveness) que el cliente completó en la app, "
                    "identificada por liveness_token. Úsala solo si el cliente aportó un token. "
                    "Devuelve {match: bool, confidence: 0.0-1.0}.",
                    VerifyBiometricsInput,
                    mocks.verify_biometrics,
                ),
                ToolSpec(
                    "check_risk_lists",
                    "Consulta listas de sanciones, PEP y listas restrictivas por nombre y documento. "
                    "Devuelve {risk_level: low|medium|high, matches: [...]}.",
                    CheckRiskListsInput,
                    mocks.check_risk_lists,
                ),
                ToolSpec(
                    "get_product_policy",
                    "Devuelve la política vigente del banco para un producto: documentos base, "
                    "edad mínima y niveles de riesgo que exigen debida diligencia reforzada.",
                    GetProductPolicyInput,
                    lambda product: mocks.get_product_policy(product, policies),
                ),
                ToolSpec(
                    "prepare_documentation",
                    "Genera la lista de documentos requeridos para abrir el producto según la "
                    "política del banco y el nivel de riesgo del cliente.",
                    PrepareDocumentationInput,
                    lambda product, client_data: mocks.prepare_documentation(
                        product, client_data, policies
                    ),
                ),
            ]
        }

    def allowed_tools(self, agent: str) -> list[str]:
        return sorted(self._allowlist.get(agent, frozenset()))

    def definitions_for(self, agent: str) -> list[dict[str, Any]]:
        return [self._tools[name].definition() for name in self.allowed_tools(agent)]

    def execute(self, agent: str, tool_use_id: str, name: str, raw_input: Any) -> ToolExecution:
        tool_input = raw_input if isinstance(raw_input, dict) else {}
        if name not in self._allowlist.get(agent, frozenset()):
            return self._result(
                agent, tool_use_id, name, tool_input,
                error=f"Tool '{name}' no autorizada para el agente '{agent}'.",
                allowed=False,
            )
        spec = self._tools.get(name)
        if spec is None:
            return self._result(agent, tool_use_id, name, tool_input, error=f"Tool '{name}' no existe.")
        try:
            params = spec.input_model.model_validate(tool_input)
        except ValidationError as e:
            return self._result(
                agent, tool_use_id, name, tool_input, error=f"Parámetros inválidos: {e.errors()}"
            )
        start = time.perf_counter()
        try:
            output = spec.handler(**params.model_dump())
        except mocks.ToolError as e:
            return self._result(
                agent, tool_use_id, name, tool_input, error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        return self._result(
            agent, tool_use_id, name, tool_input, output=output,
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def _result(
        agent: str,
        tool_use_id: str,
        name: str,
        tool_input: dict[str, Any],
        *,
        output: Any = None,
        error: str | None = None,
        allowed: bool = True,
        duration_ms: float = 0.0,
    ) -> ToolExecution:
        record = ToolCallRecord(
            agent=agent, tool=name, input=tool_input, output=output,
            allowed=allowed, error=error, duration_ms=round(duration_ms, 2),
        )
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Error: {error}" if error else json.dumps(output, ensure_ascii=False),
        }
        if error:
            block["is_error"] = True
        return ToolExecution(result_block=block, record=record)

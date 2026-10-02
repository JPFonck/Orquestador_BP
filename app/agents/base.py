"""Agente base: loop agéntico manual sobre la Messages API.

Se usa un loop manual (no el Tool Runner) para que cada `tool_use` pase por el ToolRegistry,
que aplica la allowlist del agente y deja registro de auditoría en el estado.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.llm import LLMClient, LLMError
from app.core.schema import strict_json_schema
from app.state.models import OnboardingState
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass
class AgentRun(Generic[OutputT]):
    output: OutputT | None = None
    # Última salida exitosa de cada tool: es la fuente de verdad para los guardrails,
    # independiente de cómo el LLM la haya interpretado.
    observations: dict[str, Any] = field(default_factory=dict)
    tool_errors: list[str] = field(default_factory=list)
    error: str | None = None
    retryable: bool = False
    model: str | None = None


class BaseAgent(Generic[OutputT]):
    name: str
    system_prompt: str
    output_model: type[OutputT]

    def __init__(
        self, llm: LLMClient, registry: ToolRegistry, settings: Settings, effort: str | None = None
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._effort = effort or settings.subagent_effort
        self._output_schema = strict_json_schema(self.output_model)

    async def run(self, payload: dict[str, Any], state: OnboardingState) -> AgentRun[OutputT]:
        run: AgentRun[OutputT] = AgentRun()
        tools = self._registry.definitions_for(self.name)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}
        ]

        for _ in range(self._settings.max_agent_iterations):
            try:
                response = await self._llm.create(
                    agent=self.name,
                    system=self.system_prompt,
                    messages=messages,
                    tools=tools,
                    output_schema=self._output_schema,
                    effort=self._effort,
                )
            except LLMError as e:
                run.error, run.retryable = str(e), e.retryable
                return run

            run.model = getattr(response, "model", None)
            stop_reason = response.stop_reason

            if stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                run.error = f"El modelo rechazó la solicitud (categoría: {category})."
                return run
            if stop_reason == "max_tokens":
                run.error, run.retryable = "Respuesta truncada por max_tokens.", True
                return run
            if stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if tool_uses:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": self._execute_tools(tool_uses, run, state)})
                continue

            text = "".join(b.text for b in response.content if b.type == "text")
            try:
                run.output = self.output_model.model_validate_json(text)
            except ValidationError as e:
                run.error = f"Salida estructurada inválida: {e.errors()[:3]}"
                run.retryable = True
            return run

        run.error = f"Se superó el máximo de {self._settings.max_agent_iterations} iteraciones."
        return run

    def _execute_tools(
        self, tool_uses: list[Any], run: AgentRun[OutputT], state: OnboardingState
    ) -> list[dict[str, Any]]:
        # Todos los tool_result van en un único mensaje de usuario.
        results = []
        for block in tool_uses:
            execution = self._registry.execute(self.name, block.id, block.name, block.input)
            state.tool_calls.append(execution.record)
            if not execution.record.allowed:
                state.add_event("policy_violation", agent=self.name, tool=block.name)
                logger.warning("Agente %s intentó usar tool no autorizada %s", self.name, block.name)
            if execution.ok:
                run.observations[block.name] = execution.record.output
            else:
                run.tool_errors.append(f"{block.name}: {execution.record.error}")
            results.append(execution.result_block)
        return results

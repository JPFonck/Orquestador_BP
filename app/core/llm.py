"""Cliente LLM desacoplado: los agentes dependen del Protocol, no del SDK.

Permite inyectar el cliente real (Claude vía Messages API) o el mock determinista
(sin API key) para demos y pruebas.
"""

from typing import Any, Protocol

import anthropic

from app.core.config import Settings


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    async def create(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        effort: str,
    ) -> Any:
        """Devuelve un mensaje con `.content` (bloques con `.type`), `.stop_reason` y `.model`."""
        ...


class AnthropicLLMClient:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        self._client = client or anthropic.AsyncAnthropic()

    async def create(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        effort: str,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": self._settings.max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": output_schema},
            },
        }
        if tools:
            # tool_choice queda en "auto": los modos forzados no están soportados en Opus 5.5.
            kwargs["tools"] = tools
        if self._settings.enable_fallbacks:
            # Si un clasificador de seguridad rechaza la petición, el servidor reintenta con otro modelo.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            return await self._client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise LLMError(f"Rate limit de la API: {e.message}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise LLMError(
                f"Error de la API ({e.status_code}): {e.message}", retryable=e.status_code >= 500
            ) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Error de conexión con la API: {e}", retryable=True) from e


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "mock":
        from app.core.mock_llm import MockLLMClient

        return MockLLMClient()
    return AnthropicLLMClient(settings)

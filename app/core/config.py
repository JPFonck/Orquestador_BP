from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_provider: Literal["anthropic", "mock"] = "anthropic"
    model: str = "claude-opus-5-5"
    max_tokens: int = 16000
    subagent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    decision_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    enable_fallbacks: bool = True

    # Orquestación
    max_agent_iterations: int = 6
    max_agent_retries: int = 2
    retry_backoff_seconds: float = 0.5

    # Estado
    state_backend: Literal["memory", "sqlite"] = "memory"
    sqlite_path: str = "onboarding_state.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()

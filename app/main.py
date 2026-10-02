import logging

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.llm import LLMClient, build_llm_client
from app.orchestrator.orchestrator import OnboardingOrchestrator
from app.policies.loader import load_policies
from app.state.repository import StateRepository, build_repository


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    repository: StateRepository | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Orquestador de Onboarding Digital",
        description="Coordina agentes de identidad, riesgo, documentación y respuesta al cliente.",
        version="0.1.0",
    )
    app.state.orchestrator = OnboardingOrchestrator(
        llm=llm or build_llm_client(settings),
        repository=repository or build_repository(settings.state_backend, settings.sqlite_path),
        policies=load_policies(),
        settings=settings,
    )
    app.include_router(router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "llm_provider": settings.llm_provider, "model": settings.model}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

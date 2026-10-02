import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.mock_llm import MockLLMClient
from app.main import create_app
from app.orchestrator.orchestrator import OnboardingOrchestrator
from app.policies.loader import load_policies
from app.state.models import Prospect
from app.state.repository import InMemoryStateRepository


@pytest.fixture
def settings() -> Settings:
    return Settings(llm_provider="mock", retry_backoff_seconds=0, _env_file=None)


@pytest.fixture
def policies():
    return load_policies()


@pytest.fixture
def repository() -> InMemoryStateRepository:
    return InMemoryStateRepository()


@pytest.fixture
def make_orchestrator(settings, policies, repository):
    def factory(llm=None) -> OnboardingOrchestrator:
        return OnboardingOrchestrator(
            llm=llm or MockLLMClient(), repository=repository, policies=policies, settings=settings
        )

    return factory


@pytest.fixture
def orchestrator(make_orchestrator) -> OnboardingOrchestrator:
    return make_orchestrator()


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings, llm=MockLLMClient(), repository=InMemoryStateRepository()))


def prospect(name: str = "Juan Perez", document_id: str = "1712345678", product: str = "cuenta_ahorros") -> Prospect:
    return Prospect(prospect_name=name, document_id=document_id, product=product)

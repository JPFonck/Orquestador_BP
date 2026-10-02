from app.core.config import Settings
from app.core.mock_llm import MockLLMClient
from app.orchestrator.orchestrator import OnboardingOrchestrator
from app.state.models import OnboardingState, OnboardingStatus
from app.state.repository import InMemoryStateRepository, SQLiteStateRepository
from tests.conftest import prospect


def test_in_memory_returns_copies():
    repo = InMemoryStateRepository()
    state = repo.save(OnboardingState(prospect=prospect()))
    loaded = repo.get(state.session_id)
    loaded.status = OnboardingStatus.APPROVED
    assert repo.get(state.session_id).status == OnboardingStatus.IN_PROGRESS


def test_save_increments_version():
    repo = InMemoryStateRepository()
    state = OnboardingState(prospect=prospect())
    repo.save(state)
    repo.save(state)
    assert repo.get(state.session_id).version == 2


def test_sqlite_round_trip(tmp_path):
    repo = SQLiteStateRepository(str(tmp_path / "state.db"))
    state = OnboardingState(prospect=prospect())
    state.add_event("test", foo="bar")
    repo.save(state)
    loaded = SQLiteStateRepository(str(tmp_path / "state.db")).get(state.session_id)
    assert loaded.model_dump() == state.model_dump()
    assert repo.get("no-existe") is None


async def test_session_resumes_across_orchestrator_instances(tmp_path, policies):
    """Simula un reinicio del servicio entre el inicio y el mensaje del cliente."""
    settings = Settings(llm_provider="mock", retry_backoff_seconds=0, _env_file=None)
    path = str(tmp_path / "state.db")

    def build() -> OnboardingOrchestrator:
        return OnboardingOrchestrator(MockLLMClient(), SQLiteStateRepository(path), policies, settings)

    state = await build().start(prospect("Maria Lopez", "0923456789"))
    assert state.status == OnboardingStatus.PENDING_REVIEW

    resumed = await build().handle_message(state.session_id, "Listo", liveness_token="LIVENESS-OK")
    assert resumed.status == OnboardingStatus.APPROVED
    assert len(resumed.conversation) == 3

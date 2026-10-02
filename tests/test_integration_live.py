"""Prueba de integración contra la API real de Claude.

Se excluye por defecto (ver addopts en pyproject.toml). Ejecutar con:  pytest -m live
"""

import os

import pytest

from app.core.config import Settings
from app.core.llm import AnthropicLLMClient
from app.orchestrator.orchestrator import OnboardingOrchestrator
from app.state.models import OnboardingStatus
from app.state.repository import InMemoryStateRepository
from tests.conftest import prospect

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="requiere ANTHROPIC_API_KEY"),
]


@pytest.fixture
def live_orchestrator(policies) -> OnboardingOrchestrator:
    settings = Settings(llm_provider="anthropic")
    return OnboardingOrchestrator(AnthropicLLMClient(settings), InMemoryStateRepository(), policies, settings)


async def test_live_happy_path(live_orchestrator):
    state = await live_orchestrator.start(prospect())
    assert state.status == OnboardingStatus.APPROVED
    assert state.documentation_result.status == "ok"
    assert all(call.allowed for call in state.tool_calls)
    assert state.customer_message


async def test_live_biometric_evidence_resolves_low_confidence(live_orchestrator):
    state = await live_orchestrator.start(prospect("Maria Lopez", "0923456789"))
    assert state.status == OnboardingStatus.PENDING_REVIEW
    assert [m.code for m in state.mitigations] == ["BIOMETRIC_REVERIFICATION"]

    state = await live_orchestrator.handle_message(
        state.session_id, "Ya completé la prueba de vida", liveness_token="LIVENESS-OK"
    )
    assert state.status == OnboardingStatus.APPROVED


async def test_live_high_risk_is_escalated(live_orchestrator):
    state = await live_orchestrator.start(prospect("Pedro Gomez", "1705555555", "tarjeta_credito"))
    assert state.status == OnboardingStatus.PENDING_REVIEW
    assert "COMPLIANCE_ESCALATION" in [m.code for m in state.mitigations]

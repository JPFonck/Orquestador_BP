import pytest

from app.orchestrator import guardrails
from app.state.models import IdentityResult, OnboardingStatus, RiskResult

S = OnboardingStatus


def identity(verified=True, confidence=0.95, biometric_match=None, status="ok"):
    return IdentityResult(
        status=status, verified=verified, confidence=confidence,
        effective_confidence=confidence, biometric_match=biometric_match,
    )


def risk(level="low", status="ok"):
    return RiskResult(status=status, risk_level=level)


@pytest.mark.parametrize(
    ("identity_result", "risk_result", "forced", "mitigations"),
    [
        (identity(), risk(), None, []),
        (identity(confidence=0.72), risk(), S.PENDING_REVIEW, ["BIOMETRIC_REVERIFICATION"]),
        (identity(confidence=0.4), risk(), S.PENDING_REVIEW, ["VIDEO_CALL_VERIFICATION"]),
        (identity(confidence=0.7, biometric_match=False), risk(), S.PENDING_REVIEW, ["VIDEO_CALL_VERIFICATION"]),
        (identity(verified=False, confidence=0.2), risk(), S.REJECTED, ["IN_BRANCH_VERIFICATION"]),
        (identity(), risk("high"), S.PENDING_REVIEW, ["COMPLIANCE_ESCALATION"]),
        (identity(), risk("medium"), None, ["ENHANCED_DUE_DILIGENCE"]),
        (IdentityResult(status="failed"), risk(), S.PENDING_REVIEW, ["MANUAL_REVIEW"]),
        (identity(), RiskResult(status="failed"), S.PENDING_REVIEW, ["MANUAL_REVIEW"]),
        # Varias reglas: gana la más conservadora y se acumulan las mitigaciones.
        (identity(verified=False, confidence=0.2), risk("high"), S.REJECTED,
         ["IN_BRANCH_VERIFICATION", "COMPLIANCE_ESCALATION"]),
    ],
)
def test_evaluate(policies, identity_result, risk_result, forced, mitigations):
    evaluation = guardrails.evaluate(identity_result, risk_result, policies)
    assert evaluation.forced_decision == forced
    assert evaluation.required_mitigations == mitigations


def test_threshold_is_inclusive(policies):
    evaluation = guardrails.evaluate(identity(confidence=0.8), risk(), policies)
    assert evaluation.forced_decision is None


def test_edd_flag(policies):
    assert guardrails.evaluate(identity(), risk("medium"), policies).edd_required is True


def test_enforce_overrides_llm_approval_when_rule_forces_review(policies):
    evaluation = guardrails.evaluate(identity(), risk("high"), policies)
    assert guardrails.enforce(S.APPROVED, evaluation) == (S.PENDING_REVIEW, True)


def test_enforce_accepts_conservative_choice(policies):
    evaluation = guardrails.evaluate(identity(), risk(), policies)
    assert guardrails.enforce(S.PENDING_REVIEW, evaluation) == (S.PENDING_REVIEW, False)


def test_enforce_rejection_without_rule_goes_to_review(policies):
    evaluation = guardrails.evaluate(identity(), risk(), policies)
    assert guardrails.enforce(S.REJECTED, evaluation) == (S.PENDING_REVIEW, True)


def test_enforce_without_llm_proposal(policies):
    evaluation = guardrails.evaluate(identity(), risk(), policies)
    assert guardrails.enforce(None, evaluation) == (S.PENDING_REVIEW, True)

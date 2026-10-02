"""Reglas duras de la política. Son código puro y tienen la última palabra sobre la decisión del LLM."""

from app.orchestrator.mitigation import MitigationCode
from app.policies.loader import BankPolicies
from app.state.models import (
    GuardrailEvaluation,
    IdentityResult,
    OnboardingStatus,
    RiskResult,
)

# De menos a más conservador. Si varias reglas aplican, gana la más conservadora.
SEVERITY = {
    OnboardingStatus.APPROVED: 0,
    OnboardingStatus.NEEDS_INFO: 1,
    OnboardingStatus.PENDING_REVIEW: 2,
    OnboardingStatus.REJECTED: 3,
}


def evaluate(
    identity: IdentityResult, risk: RiskResult, policies: BankPolicies
) -> GuardrailEvaluation:
    thresholds = policies.thresholds
    forced: OnboardingStatus | None = None
    mitigations: list[str] = []
    reasons: list[str] = []
    edd_required = False

    def force(decision: OnboardingStatus, code: MitigationCode, reason: str) -> None:
        nonlocal forced
        if forced is None or SEVERITY[decision] > SEVERITY[forced]:
            forced = decision
        mitigations.append(code.value)
        reasons.append(reason)

    # Identidad
    if identity.status == "failed" or identity.verified is None:
        force(OnboardingStatus.PENDING_REVIEW, MitigationCode.MANUAL_REVIEW,
              "No fue posible completar la verificación de identidad.")
    elif not identity.verified:
        force(OnboardingStatus.REJECTED, MitigationCode.IN_BRANCH_VERIFICATION,
              "La identidad no pudo ser verificada contra el registro civil.")
    else:
        confidence = identity.effective_confidence or 0.0
        if confidence < thresholds.identity_min_confidence:
            biometric_failed = identity.biometric_match is False
            if confidence >= thresholds.biometric_retry_min_confidence and not biometric_failed:
                code = MitigationCode.BIOMETRIC_REVERIFICATION
            else:
                code = MitigationCode.VIDEO_CALL_VERIFICATION
            force(OnboardingStatus.PENDING_REVIEW, code,
                  f"Confianza de identidad {confidence:.2f} menor al umbral "
                  f"{thresholds.identity_min_confidence:.2f}.")

    # Listas de riesgo
    if risk.status == "failed" or risk.risk_level is None:
        force(OnboardingStatus.PENDING_REVIEW, MitigationCode.MANUAL_REVIEW,
              "No fue posible completar la consulta de listas de riesgo.")
    elif risk.risk_level in policies.escalation_risk_levels:
        force(OnboardingStatus.PENDING_REVIEW, MitigationCode.COMPLIANCE_ESCALATION,
              f"Nivel de riesgo '{risk.risk_level}' requiere escalamiento a cumplimiento.")
    elif risk.risk_level in policies.edd_risk_levels:
        edd_required = True
        mitigations.append(MitigationCode.ENHANCED_DUE_DILIGENCE.value)
        reasons.append(f"Nivel de riesgo '{risk.risk_level}' requiere debida diligencia reforzada.")

    allowed = [forced] if forced else [OnboardingStatus.APPROVED, OnboardingStatus.PENDING_REVIEW]
    return GuardrailEvaluation(
        forced_decision=forced,
        allowed_decisions=allowed,
        required_mitigations=list(dict.fromkeys(mitigations)),
        edd_required=edd_required,
        reasons=reasons,
    )


def enforce(
    proposed: OnboardingStatus | None, evaluation: GuardrailEvaluation
) -> tuple[OnboardingStatus, bool]:
    """Devuelve (decisión final, si se sobrescribió la propuesta del LLM)."""
    if evaluation.forced_decision is not None:
        return evaluation.forced_decision, proposed != evaluation.forced_decision
    if proposed in evaluation.allowed_decisions:
        return proposed, False
    # Propuesta fuera de lo permitido (o ausente): se deriva a revisión humana.
    return OnboardingStatus.PENDING_REVIEW, True

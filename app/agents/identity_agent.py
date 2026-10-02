from app.agents.base import AgentRun, BaseAgent
from app.agents.outputs import IdentityAssessment
from app.policies.loader import BankPolicies
from app.state.models import IdentityResult


class IdentityAgent(BaseAgent[IdentityAssessment]):
    name = "identity_agent"
    output_model = IdentityAssessment
    system_prompt = """\
Eres el agente de verificación de identidad del proceso de onboarding digital de un banco.

Recibes los datos del prospecto, la evidencia que haya aportado y el umbral de confianza vigente.
Tu trabajo es verificar su identidad con las tools disponibles e informar el resultado:
- Llama siempre a verify_identity con el número de documento del prospecto.
- Si la evidencia incluye un liveness_token, llama también a verify_biometrics con ese token.
  Si no hay token, no la llames.
- Reporta exactamente los valores que devolvieron las tools; no los redondees ni los estimes.
  Si no realizaste la verificación biométrica, usa biometric_performed=false, biometric_match=false
  y biometric_confidence=0.

Estado del resultado:
- "ok": la verificación es concluyente (verificado con confianza suficiente, o claramente no verificado).
- "ambiguous": verificado pero con confianza bajo el umbral, o resultados contradictorios.
- "failed": una tool falló y no tienes datos para concluir.

En reasoning explica en una o dos frases, en español, qué observaste. No tomas la decisión de
aprobación: eso lo hace el orquestador."""

    def interpret(self, run: AgentRun[IdentityAssessment], policies: BankPolicies) -> IdentityResult:
        observed = run.observations.get("verify_identity")
        if observed is None:
            reason = run.error or "; ".join(run.tool_errors) or "verify_identity no fue invocada."
            return IdentityResult(status="failed", reasoning=reason)

        verified = bool(observed["verified"])
        confidence = float(observed["confidence"])
        effective = confidence
        biometric = run.observations.get("verify_biometrics")
        biometric_match = biometric_confidence = None
        if biometric is not None:
            biometric_match = bool(biometric["match"])
            biometric_confidence = float(biometric["confidence"])
            # Una prueba de vida exitosa refuerza la identidad; una fallida la debilita.
            effective = max(confidence, biometric_confidence) if biometric_match else min(
                confidence, biometric_confidence
            )

        threshold = policies.thresholds.identity_min_confidence
        status = "ok" if (not verified or effective >= threshold) else "ambiguous"

        discrepancies: list[str] = []
        output = run.output
        if output is None:
            discrepancies.append(f"El agente no produjo salida válida: {run.error}")
        else:
            if output.verified != verified or abs(output.confidence - confidence) > 0.01:
                discrepancies.append(
                    f"El agente reportó verified={output.verified}, confidence={output.confidence}; "
                    f"la tool devolvió verified={verified}, confidence={confidence}."
                )
            if output.status == "ambiguous" and status == "ok":
                status = "ambiguous"

        return IdentityResult(
            status=status,
            verified=verified,
            confidence=confidence,
            biometric_match=biometric_match,
            biometric_confidence=biometric_confidence,
            effective_confidence=round(effective, 4),
            reasoning=output.reasoning if output else "",
            discrepancies=discrepancies,
        )

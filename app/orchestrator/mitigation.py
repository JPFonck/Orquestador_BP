"""Catálogo cerrado de mitigaciones que el orquestador puede proponer.

El LLM elige y justifica, pero solo dentro de este catálogo: así cada propuesta corresponde
a un procedimiento real del banco.
"""

from enum import StrEnum

from app.state.models import Mitigation


class MitigationCode(StrEnum):
    BIOMETRIC_REVERIFICATION = "BIOMETRIC_REVERIFICATION"
    VIDEO_CALL_VERIFICATION = "VIDEO_CALL_VERIFICATION"
    IN_BRANCH_VERIFICATION = "IN_BRANCH_VERIFICATION"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    COMPLIANCE_ESCALATION = "COMPLIANCE_ESCALATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REQUEST_VALID_PRODUCT = "REQUEST_VALID_PRODUCT"


CATALOG: dict[MitigationCode, Mitigation] = {
    MitigationCode.BIOMETRIC_REVERIFICATION: Mitigation(
        code=MitigationCode.BIOMETRIC_REVERIFICATION,
        title="Re-verificación biométrica",
        description="La confianza de la verificación de identidad es menor al umbral. Se solicita "
        "prueba de vida (selfie + liveness) y se vuelve a verificar.",
        customer_action="Completa la prueba de vida desde la app y envíanos el código de verificación.",
        owner="customer",
    ),
    MitigationCode.VIDEO_CALL_VERIFICATION: Mitigation(
        code=MitigationCode.VIDEO_CALL_VERIFICATION,
        title="Verificación por videollamada",
        description="La confianza es demasiado baja para la verificación automática. Un asesor "
        "valida la identidad por videollamada.",
        customer_action="Agenda una videollamada con un asesor para validar tu identidad.",
        owner="customer",
    ),
    MitigationCode.IN_BRANCH_VERIFICATION: Mitigation(
        code=MitigationCode.IN_BRANCH_VERIFICATION,
        title="Validación presencial",
        description="No fue posible verificar la identidad de forma digital. El proceso debe "
        "completarse presencialmente.",
        customer_action="Acércate a cualquier agencia con tu cédula original para completar el proceso.",
        owner="customer",
    ),
    MitigationCode.ENHANCED_DUE_DILIGENCE: Mitigation(
        code=MitigationCode.ENHANCED_DUE_DILIGENCE,
        title="Debida diligencia reforzada",
        description="El nivel de riesgo es medio. Se solicitan documentos adicionales "
        "(origen de fondos, actividad económica) antes de activar el producto.",
        customer_action="Adjunta la documentación adicional indicada en la lista de requisitos.",
        owner="customer",
    ),
    MitigationCode.COMPLIANCE_ESCALATION: Mitigation(
        code=MitigationCode.COMPLIANCE_ESCALATION,
        title="Escalamiento a Oficial de Cumplimiento",
        description="Coincidencia en listas de riesgo alto. El producto no se activa hasta la "
        "revisión del Oficial de Cumplimiento.",
        customer_action=None,
        owner="compliance",
    ),
    MitigationCode.MANUAL_REVIEW: Mitigation(
        code=MitigationCode.MANUAL_REVIEW,
        title="Revisión manual",
        description="Una verificación falló tras los reintentos automáticos (servicio no disponible "
        "o resultado no concluyente). Un analista completa la verificación manualmente.",
        customer_action=None,
        owner="bank",
    ),
    MitigationCode.REQUEST_VALID_PRODUCT: Mitigation(
        code=MitigationCode.REQUEST_VALID_PRODUCT,
        title="Producto no disponible",
        description="El producto solicitado no está en el catálogo de onboarding digital.",
        customer_action="Elige uno de los productos disponibles para continuar.",
        owner="customer",
    ),
}

# Mitigaciones que implican que la solicitud no puede aprobarse todavía.
BLOCKING = {
    MitigationCode.BIOMETRIC_REVERIFICATION,
    MitigationCode.VIDEO_CALL_VERIFICATION,
    MitigationCode.IN_BRANCH_VERIFICATION,
    MitigationCode.COMPLIANCE_ESCALATION,
    MitigationCode.MANUAL_REVIEW,
    MitigationCode.REQUEST_VALID_PRODUCT,
}


def resolve(codes: list[str]) -> list[Mitigation]:
    seen: list[MitigationCode] = []
    for code in codes:
        try:
            parsed = MitigationCode(code)
        except ValueError:
            continue
        if parsed not in seen:
            seen.append(parsed)
    return [CATALOG[code].model_copy() for code in seen]

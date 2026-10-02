import re
from typing import Any

from app.agents.base import BaseAgent
from app.agents.outputs import CustomerMessageOutput

# Información interna de cumplimiento que nunca debe llegar al cliente.
INTERNAL_TERMS = re.compile(
    r"\b(pep|ofac|onu)\b|sancion|sanción|lavado de|terroris|listas? (restrictivas?|de riesgo)|"
    r"nivel de riesgo|persona expuesta",
    re.IGNORECASE,
)


def leaks_internal_info(message: str) -> bool:
    return bool(INTERNAL_TERMS.search(message))


def render_template_message(payload: dict[str, Any]) -> str:
    """Mensaje determinista de respaldo si el agente falla o su texto no pasa el filtro."""
    name = payload["customer_name"]
    product = payload["product_name"] or "el producto solicitado"
    decision = payload["decision"]
    actions = payload.get("customer_actions") or []
    documents = payload.get("required_documents") or []
    doc_lines = "".join(f"\n- {doc['name']}" for doc in documents)
    action_lines = "".join(f"\n- {action}" for action in actions)

    if decision == "APPROVED":
        text = (
            f"Hola {name}, tu solicitud de {product} fue aprobada. Para continuar, carga los "
            f"siguientes documentos:{doc_lines}"
        )
    elif decision == "REJECTED":
        text = (
            f"Hola {name}, por ahora no es posible completar tu solicitud de {product} por el "
            "canal digital."
        )
    elif decision == "NEEDS_INFO":
        available = ", ".join(payload.get("available_products") or [])
        text = f"Hola {name}, necesitamos más información para continuar con tu solicitud."
        if available:
            text += f" Los productos disponibles son: {available}."
    else:
        text = f"Hola {name}, tu solicitud de {product} sigue en proceso."
        if not actions:
            text += " Nuestro equipo la está revisando y te contactaremos pronto."
    if actions:
        text += f"\nPasos a seguir:{action_lines}"
    return text


class ResponseAgent(BaseAgent[CustomerMessageOutput]):
    name = "response_agent"
    output_model = CustomerMessageOutput
    system_prompt = """\
Eres el asistente que se comunica con el cliente durante su onboarding digital en el banco.

Recibes la decisión, el producto, los documentos requeridos, las acciones que el cliente debe
realizar y, si existe, el historial de la conversación y el último mensaje del cliente. Redacta
un único mensaje en español, cordial y claro, dirigido al cliente por su nombre:
- APPROVED: confirma que puede continuar y lista los documentos a cargar.
- PENDING_REVIEW: explica que la solicitud sigue en proceso y qué debe hacer (si hay acciones del
  cliente) o que el banco la está revisando y le contactará.
- REJECTED: explica con tacto que no es posible continuar por el canal digital y qué alternativa tiene.
- NEEDS_INFO: indica qué información falta.
Si el cliente hizo una pregunta, respóndela con la información disponible.

Confidencialidad: nunca menciones listas de sanciones, PEP, niveles de riesgo, puntajes de
confianza ni motivos internos de cumplimiento. No prometas plazos ni condiciones que no estén
en los datos recibidos."""

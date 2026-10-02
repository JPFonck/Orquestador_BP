from app.agents.base import AgentRun, BaseAgent
from app.agents.outputs import DocumentationAssessment
from app.state.models import DocumentationResult, RequiredDocument


class DocumentationAgent(BaseAgent[DocumentationAssessment]):
    name = "documentation_agent"
    output_model = DocumentationAssessment
    system_prompt = """\
Eres el agente de documentación del proceso de onboarding digital de un banco.

Recibes el producto solicitado, los datos del cliente y su nivel de riesgo. Prepara la lista de
documentos requeridos según la política vigente:
- Consulta la política del producto con get_product_policy.
- Genera la lista con prepare_documentation, pasando el nivel de riesgo recibido en client_data.
- En required_document_codes devuelve los códigos exactamente como los devolvió prepare_documentation.
- En notes indica, en español y en una o dos frases, si aplica debida diligencia reforzada y
  cualquier requisito de la política que el cliente deba tener presente (p. ej. edad mínima).

Estado: "ok" si obtuviste la lista; "failed" si las tools fallaron."""

    def interpret(self, run: AgentRun[DocumentationAssessment]) -> DocumentationResult:
        observed = run.observations.get("prepare_documentation")
        if observed is None:
            reason = run.error or "; ".join(run.tool_errors) or "prepare_documentation no fue invocada."
            return DocumentationResult(status="failed", notes=reason)
        return DocumentationResult(
            status="ok",
            required_documents=[RequiredDocument(**doc) for doc in observed["required_documents"]],
            notes=run.output.notes if run.output else "",
        )

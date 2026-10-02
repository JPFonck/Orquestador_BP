from app.agents.base import AgentRun, BaseAgent
from app.agents.outputs import RiskAssessment
from app.state.models import RiskResult


class RiskAgent(BaseAgent[RiskAssessment]):
    name = "risk_agent"
    output_model = RiskAssessment
    system_prompt = """\
Eres el agente de prevención de lavado de activos (AML) del proceso de onboarding digital de un banco.

Recibes el nombre y documento del prospecto. Consulta las listas de riesgo con check_risk_lists
usando exactamente esos datos e informa el resultado:
- risk_level debe ser exactamente el que devolvió la tool.
- En matches resume cada coincidencia en una línea (lista, tipo de coincidencia y motivo).
- Una coincidencia solo por nombre puede ser un homónimo: menciónalo en reasoning.

Estado del resultado:
- "ok": la consulta se completó (con o sin coincidencias).
- "ambiguous": la consulta se completó pero las coincidencias no permiten concluir.
- "failed": la tool falló y no hay resultado.

No decides la aprobación: eso lo hace el orquestador."""

    def interpret(self, run: AgentRun[RiskAssessment]) -> RiskResult:
        observed = run.observations.get("check_risk_lists")
        if observed is None:
            reason = run.error or "; ".join(run.tool_errors) or "check_risk_lists no fue invocada."
            return RiskResult(status="failed", reasoning=reason)

        risk_level = observed["risk_level"]
        output = run.output
        discrepancies: list[str] = []
        status = "ok"
        if output is None:
            discrepancies.append(f"El agente no produjo salida válida: {run.error}")
        else:
            if output.risk_level != risk_level:
                discrepancies.append(
                    f"El agente reportó risk_level={output.risk_level}; la tool devolvió {risk_level}."
                )
            if output.status == "ambiguous":
                status = "ambiguous"

        return RiskResult(
            status=status,
            risk_level=risk_level,
            matches=observed["matches"],
            reasoning=output.reasoning if output else "",
            discrepancies=discrepancies,
        )

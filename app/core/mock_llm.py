"""LLM simulado y determinista (LLM_PROVIDER=mock).

Imita el comportamiento esperado de Claude en cada agente — primero pide las tools, luego
devuelve la salida estructurada a partir de los resultados — para poder ejecutar el flujo
completo sin API key (demo local y pruebas automatizadas).
"""

import itertools
import json
from dataclasses import dataclass
from typing import Any

from app.agents.response_agent import render_template_message


@dataclass
class MockBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class MockMessage:
    content: list[MockBlock]
    stop_reason: str
    model: str = "mock-llm"
    stop_details: Any = None


def tool_use(calls: list[tuple[str, dict[str, Any]]], counter: Any) -> MockMessage:
    blocks = [
        MockBlock(type="tool_use", id=f"toolu_mock_{next(counter)}", name=name, input=args)
        for name, args in calls
    ]
    return MockMessage(content=blocks, stop_reason="tool_use")


def final(output: dict[str, Any]) -> MockMessage:
    return MockMessage(
        content=[MockBlock(type="text", text=json.dumps(output, ensure_ascii=False))],
        stop_reason="end_turn",
    )


def tool_results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mapea nombre de tool -> {"error": bool, "data": ...} a partir del historial."""
    names: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    for message in messages:
        content = message["content"]
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, MockBlock) and block.type == "tool_use":
                names[block.id] = block.name
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                is_error = block.get("is_error", False)
                data = block["content"] if is_error else json.loads(block["content"])
                results[names.get(block["tool_use_id"], "?")] = {"error": is_error, "data": data}
    return results


class MockLLMClient:
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def create(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        effort: str,
    ) -> MockMessage:
        payload = json.loads(messages[0]["content"])
        return getattr(self, f"_{agent}")(payload, tool_results(messages))

    def _identity_agent(self, payload: dict[str, Any], results: dict[str, Any]) -> MockMessage:
        document_id = payload["prospect"]["document_id"]
        token = (payload.get("evidence") or {}).get("liveness_token")
        if not results:
            calls = [("verify_identity", {"document_id": document_id})]
            if token:
                calls.append(("verify_biometrics", {"document_id": document_id, "liveness_token": token}))
            return tool_use(calls, self._ids)

        identity = results.get("verify_identity", {"error": True, "data": "sin resultado"})
        biometric = results.get("verify_biometrics")
        if identity["error"]:
            return final({
                "status": "failed", "verified": False, "confidence": 0.0,
                "biometric_performed": False, "biometric_match": False, "biometric_confidence": 0.0,
                "reasoning": f"No se pudo verificar la identidad: {identity['data']}",
            })
        data = identity["data"]
        bio_ok = biometric is not None and not biometric["error"]
        effective = data["confidence"]
        if bio_ok:
            effective = (max if biometric["data"]["match"] else min)(effective, biometric["data"]["confidence"])
        threshold = payload["policy"]["identity_min_confidence"]
        status = "ok" if (not data["verified"] or effective >= threshold) else "ambiguous"
        return final({
            "status": status,
            "verified": data["verified"],
            "confidence": data["confidence"],
            "biometric_performed": bio_ok,
            "biometric_match": bool(bio_ok and biometric["data"]["match"]),
            "biometric_confidence": biometric["data"]["confidence"] if bio_ok else 0.0,
            "reasoning": f"Registro civil: verified={data['verified']}, confianza {data['confidence']}"
            + (f"; prueba de vida: confianza {biometric['data']['confidence']}" if bio_ok else "") + ".",
        })

    def _risk_agent(self, payload: dict[str, Any], results: dict[str, Any]) -> MockMessage:
        prospect = payload["prospect"]
        if not results:
            return tool_use(
                [("check_risk_lists", {"name": prospect["name"], "document_id": prospect["document_id"]})],
                self._ids,
            )
        risk = results["check_risk_lists"]
        if risk["error"]:
            return final({"status": "failed", "risk_level": "low", "matches": [], "reasoning": risk["data"]})
        data = risk["data"]
        matches = [f"{m['list']} ({m['matched_on']}): {m['reason']}" for m in data["matches"]]
        return final({
            "status": "ok",
            "risk_level": data["risk_level"],
            "matches": matches,
            "reasoning": f"Consulta completada con {len(matches)} coincidencia(s).",
        })

    def _documentation_agent(self, payload: dict[str, Any], results: dict[str, Any]) -> MockMessage:
        if not results:
            return tool_use(
                [
                    ("get_product_policy", {"product": payload["product"]}),
                    ("prepare_documentation", {"product": payload["product"], "client_data": payload["client_data"]}),
                ],
                self._ids,
            )
        docs = results["prepare_documentation"]
        if docs["error"]:
            return final({"status": "failed", "required_document_codes": [], "notes": docs["data"]})
        data = docs["data"]
        return final({
            "status": "ok",
            "required_document_codes": [d["code"] for d in data["required_documents"]],
            "notes": "Aplica debida diligencia reforzada." if data["enhanced_due_diligence"]
            else "Documentación estándar del producto.",
        })

    def _decision_agent(self, payload: dict[str, Any], results: dict[str, Any]) -> MockMessage:
        rules = payload["guardrails"]
        decision = rules["forced_decision"] or rules["allowed_decisions"][0]
        reasons = rules["reasons"] or ["Identidad verificada y sin coincidencias en listas de riesgo."]
        return final({
            "decision": decision,
            "mitigation_codes": rules["required_mitigations"],
            "rationale": " ".join(reasons),
        })

    def _response_agent(self, payload: dict[str, Any], results: dict[str, Any]) -> MockMessage:
        return final({"message": render_template_message(payload)})

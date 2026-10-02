"""Implementaciones simuladas de los servicios externos (registro civil, listas de riesgo, gestor documental).

Son deterministas: el comportamiento depende de los datos en app/data/*.json, lo que permite
reproducir cada escenario (aprobado, ambiguo, riesgo alto, timeout...) en pruebas.
"""

import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.policies.loader import BankPolicies, load_policies

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Códigos de provincia válidos para cédulas ecuatorianas (01-24) y 30 (ecuatorianos en el exterior).
VALID_PROVINCES = {f"{i:02d}" for i in range(1, 25)} | {"30"}


class ToolError(Exception):
    """Error controlado de una tool; se devuelve al agente como tool_result con is_error."""


class ToolTimeoutError(ToolError):
    pass


@lru_cache
def _load(name: str) -> dict[str, Any]:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def normalize_name(name: str) -> str:
    without_accents = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(without_accents.lower().split())


def is_valid_document_format(document_id: str) -> bool:
    return (
        len(document_id) == 10
        and document_id.isdigit()
        and document_id[:2] in VALID_PROVINCES
        and int(document_id[2]) < 6
    )


def verify_identity(document_id: str) -> dict[str, Any]:
    registry = _load("mock_registry.json")
    if document_id in registry["timeout_documents"]:
        raise ToolTimeoutError("El servicio de registro civil no respondió a tiempo (timeout 5s).")
    if not is_valid_document_format(document_id):
        return {"verified": False, "confidence": 0.0}
    record = registry["identities"].get(document_id)
    if record is None:
        return {"verified": True, "confidence": 0.9}
    return {"verified": record["verified"], "confidence": record["confidence"]}


def verify_biometrics(document_id: str, liveness_token: str) -> dict[str, Any]:
    registry = _load("mock_registry.json")
    result = registry["liveness_tokens"].get(liveness_token)
    if result is None:
        raise ToolError(f"Token de prueba de vida desconocido o expirado: {liveness_token!r}")
    return {"match": result["match"], "confidence": result["confidence"]}


def check_risk_lists(name: str, document_id: str) -> dict[str, Any]:
    entries = _load("mock_risk_lists.json")["entries"]
    target = normalize_name(name)
    matches: list[dict[str, Any]] = []
    levels: list[str] = []
    for entry in entries:
        by_document = entry["document_id"] == document_id
        by_name = normalize_name(entry["name"]) == target
        if not (by_document or by_name):
            continue
        # Coincidencia solo por nombre: posible homónimo, se trata como riesgo medio.
        level = entry["risk_level"] if by_document else "medium"
        levels.append(level)
        matches.append(
            {
                "list": entry["list"],
                "matched_on": "document_id" if by_document else "name_only",
                "risk_level": level,
                "reason": entry["reason"],
            }
        )
    order = {"low": 0, "medium": 1, "high": 2}
    risk_level = max(levels, key=order.__getitem__) if levels else "low"
    return {"risk_level": risk_level, "matches": matches}


def get_product_policy(product: str, policies: BankPolicies | None = None) -> dict[str, Any]:
    policies = policies or load_policies()
    policy = policies.product(product)
    if policy is None:
        raise ToolError(
            f"Producto no soportado: {product!r}. Disponibles: {', '.join(policies.products)}"
        )
    return {
        "product": product,
        "policy_version": policies.version,
        **policy.model_dump(),
        "edd_risk_levels": policies.edd_risk_levels,
        "escalation_risk_levels": policies.escalation_risk_levels,
    }


def prepare_documentation(
    product: str, client_data: dict[str, Any], policies: BankPolicies | None = None
) -> dict[str, Any]:
    policies = policies or load_policies()
    policy = policies.product(product)
    if policy is None:
        raise ToolError(f"Producto no soportado: {product!r}")
    documents = [doc.model_dump() for doc in policy.base_documents]
    risk_level = client_data.get("risk_level", "low")
    if risk_level in policies.edd_risk_levels:
        documents += [doc.model_dump() for doc in policies.risk_documents.get(risk_level, [])]
    return {
        "product": product,
        "policy_version": policies.version,
        "required_documents": documents,
        "enhanced_due_diligence": risk_level in policies.edd_risk_levels,
    }

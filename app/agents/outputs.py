"""Salidas estructuradas de cada agente (se envían como `output_config.format` a la API).

Todos los campos son obligatorios y sin valores por defecto: es lo que exige el modo estricto.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.orchestrator.mitigation import MitigationCode


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityAssessment(_Output):
    status: Literal["ok", "ambiguous", "failed"]
    verified: bool
    confidence: float
    biometric_performed: bool
    biometric_match: bool
    biometric_confidence: float
    reasoning: str


class RiskAssessment(_Output):
    status: Literal["ok", "ambiguous", "failed"]
    risk_level: Literal["low", "medium", "high"]
    matches: list[str]
    reasoning: str


class DocumentationAssessment(_Output):
    status: Literal["ok", "ambiguous", "failed"]
    required_document_codes: list[str]
    notes: str


class DecisionOutput(_Output):
    decision: Literal["APPROVED", "REJECTED", "PENDING_REVIEW", "NEEDS_INFO"]
    mitigation_codes: list[MitigationCode]
    rationale: str


class CustomerMessageOutput(_Output):
    message: str

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.state.models import (
    DecisionRecord,
    Mitigation,
    OnboardingState,
    OnboardingStatus,
    RequiredDocument,
    Step,
)


class StartOnboardingRequest(BaseModel):
    prospect_name: str = Field(min_length=3, max_length=120, examples=["Juan Perez"])
    document_id: str = Field(min_length=5, max_length=20, examples=["1712345678"])
    product: str = Field(min_length=2, max_length=50, examples=["cuenta_ahorros"])

    @field_validator("prospect_name", "document_id", "product")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()


class CustomerMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    liveness_token: str | None = Field(
        default=None, description="Token de la prueba de vida completada en la app."
    )
    product: str | None = Field(default=None, description="Producto corregido, si aplica.")


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=2, max_length=120)
    notes: str = Field(min_length=3, max_length=2000)


class OnboardingResponse(BaseModel):
    session_id: str
    status: OnboardingStatus
    current_step: Step
    decision: DecisionRecord | None
    required_documents: list[RequiredDocument]
    mitigations: list[Mitigation]
    customer_message: str | None

    @classmethod
    def from_state(cls, state: OnboardingState) -> "OnboardingResponse":
        return cls(
            session_id=state.session_id,
            status=state.status,
            current_step=state.current_step,
            decision=state.decision,
            required_documents=state.documentation_result.required_documents
            if state.documentation_result
            else [],
            mitigations=state.mitigations,
            customer_message=state.customer_message,
        )

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OnboardingStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    NEEDS_INFO = "NEEDS_INFO"


class Step(StrEnum):
    RECEIVED = "RECEIVED"
    IDENTITY_AND_RISK = "IDENTITY_AND_RISK"
    GUARDRAILS = "GUARDRAILS"
    DOCUMENTATION = "DOCUMENTATION"
    DECISION = "DECISION"
    RESPONSE = "RESPONSE"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"


AgentStatus = Literal["ok", "ambiguous", "failed"]
RiskLevel = Literal["low", "medium", "high"]


class Prospect(BaseModel):
    prospect_name: str
    document_id: str
    product: str


class ToolCallRecord(BaseModel):
    agent: str
    tool: str
    input: dict[str, Any]
    output: Any = None
    allowed: bool
    error: str | None = None
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=utcnow)


class Event(BaseModel):
    type: str
    step: Step | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class ConversationTurn(BaseModel):
    role: Literal["customer", "assistant", "reviewer"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class IdentityResult(BaseModel):
    status: AgentStatus
    verified: bool | None = None
    confidence: float | None = None
    biometric_match: bool | None = None
    biometric_confidence: float | None = None
    effective_confidence: float | None = None
    reasoning: str = ""
    discrepancies: list[str] = Field(default_factory=list)


class RiskResult(BaseModel):
    status: AgentStatus
    risk_level: RiskLevel | None = None
    matches: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str = ""
    discrepancies: list[str] = Field(default_factory=list)


class RequiredDocument(BaseModel):
    code: str
    name: str
    description: str


class DocumentationResult(BaseModel):
    status: AgentStatus
    required_documents: list[RequiredDocument] = Field(default_factory=list)
    notes: str = ""


class Mitigation(BaseModel):
    code: str
    title: str
    description: str
    customer_action: str | None = None
    owner: Literal["customer", "bank", "compliance"]


class GuardrailEvaluation(BaseModel):
    forced_decision: OnboardingStatus | None = None
    allowed_decisions: list[OnboardingStatus]
    required_mitigations: list[str] = Field(default_factory=list)
    edd_required: bool = False
    reasons: list[str] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    decision: OnboardingStatus
    rationale: str
    llm_decision: OnboardingStatus | None = None
    overridden_by_guardrails: bool = False
    decided_by: Literal["orchestrator", "reviewer"] = "orchestrator"


class OnboardingState(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: OnboardingStatus = OnboardingStatus.IN_PROGRESS
    current_step: Step = Step.RECEIVED
    prospect: Prospect
    evidence: dict[str, Any] = Field(default_factory=dict)
    identity_result: IdentityResult | None = None
    risk_result: RiskResult | None = None
    guardrails: GuardrailEvaluation | None = None
    documentation_result: DocumentationResult | None = None
    decision: DecisionRecord | None = None
    mitigations: list[Mitigation] = Field(default_factory=list)
    customer_message: str | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    conversation: list[ConversationTurn] = Field(default_factory=list)
    version: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def add_event(self, type: str, step: Step | None = None, **detail: Any) -> None:
        self.events.append(Event(type=type, step=step or self.current_step, detail=detail))

    def move_to(self, step: Step) -> None:
        self.add_event("step_started", step=step, previous=self.current_step.value)
        self.current_step = step

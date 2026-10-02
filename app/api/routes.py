from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.schemas import (
    CustomerMessageRequest,
    OnboardingResponse,
    ReviewRequest,
    StartOnboardingRequest,
)
from app.orchestrator.orchestrator import (
    InvalidTransitionError,
    OnboardingOrchestrator,
    SessionNotFoundError,
)
from app.state.models import OnboardingState, Prospect

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


def get_orchestrator(request: Request) -> OnboardingOrchestrator:
    return request.app.state.orchestrator


def _not_found(session_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Sesión {session_id} no encontrada.")


@router.post("/start", response_model=OnboardingResponse, status_code=status.HTTP_201_CREATED)
async def start_onboarding(
    body: StartOnboardingRequest, orchestrator: OnboardingOrchestrator = Depends(get_orchestrator)
) -> OnboardingResponse:
    state = await orchestrator.start(Prospect(**body.model_dump()))
    return OnboardingResponse.from_state(state)


@router.get("/{session_id}", response_model=OnboardingState)
async def get_onboarding(
    session_id: str, orchestrator: OnboardingOrchestrator = Depends(get_orchestrator)
) -> OnboardingState:
    try:
        return orchestrator.get(session_id)
    except SessionNotFoundError:
        raise _not_found(session_id)


@router.post("/{session_id}/messages", response_model=OnboardingResponse)
async def post_message(
    session_id: str,
    body: CustomerMessageRequest,
    orchestrator: OnboardingOrchestrator = Depends(get_orchestrator),
) -> OnboardingResponse:
    try:
        state = await orchestrator.handle_message(
            session_id, body.message, liveness_token=body.liveness_token, product=body.product
        )
    except SessionNotFoundError:
        raise _not_found(session_id)
    return OnboardingResponse.from_state(state)


@router.post("/{session_id}/review", response_model=OnboardingResponse)
async def review_onboarding(
    session_id: str,
    body: ReviewRequest,
    orchestrator: OnboardingOrchestrator = Depends(get_orchestrator),
) -> OnboardingResponse:
    try:
        state = await orchestrator.review(
            session_id, approve=body.decision == "approve", reviewer=body.reviewer, notes=body.notes
        )
    except SessionNotFoundError:
        raise _not_found(session_id)
    except InvalidTransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return OnboardingResponse.from_state(state)

"""Session CRUD routes.

This module handles session creation and closing.
For message handling, see session_messages.py
For analysis endpoints, see session_analysis.py
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, StrictInt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session
from src.api.routes.session_analysis import router as analysis_router
from src.api.routes.session_helpers import (
    load_session,
    mark_session_ended,
)

# Import sub-routers to include all session routes
from src.api.routes.session_messages import router as messages_router
from src.api.routes.student_generation import router as student_router
from src.config import config
from src.models import User
from src.services.lesson_snapshots import start_lesson

router = APIRouter(tags=["Sessions"])
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)

# Include sub-routers
router.include_router(messages_router)
router.include_router(analysis_router)
router.include_router(student_router)


class CreateSessionRequest(BaseModel):
    """Request schema for creating session."""

    model_config = ConfigDict(extra="forbid")
    scenario_id: StrictInt


class SessionResponse(BaseModel):
    """Response schema for session creation."""

    id: int
    scenario_id: int
    started_at: str


class CloseSessionResponse(BaseModel):
    """Response schema for session close operation."""

    status: str
    ended_at: str
    already_ended: bool


@router.post("/sessions", status_code=201)
@limiter.limit("10/minute")
async def create_session(
    request: Request,
    data: CreateSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """Start new dialogue session."""
    session = await start_lesson(db, data.scenario_id, user)

    return SessionResponse(
        id=session.id,
        scenario_id=session.scenario_id,
        started_at=session.started_at.isoformat(),
    )


@router.post("/sessions/{session_id}/close")
@limiter.limit("30/minute")
async def close_session(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CloseSessionResponse:
    """Close session without analysis (lightweight termination).

    This endpoint is idempotent - calling it multiple times will
    succeed without error. Used when user navigates away from chat
    without clicking the end button.

    Returns:
        CloseSessionResponse with ended_at timestamp and already_ended flag
    """
    session = await load_session(session_id, user, db)

    ended_at, already_ended = await mark_session_ended(session, db, force=True)

    return CloseSessionResponse(
        status="ended",
        ended_at=ended_at.isoformat(),
        already_ended=already_ended,
    )

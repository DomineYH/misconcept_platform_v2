"""Scenario browsing and selection routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.api.routes.session_helpers import validate_scenario_access
from src.models import Scenario, Session, User
from src.models.scenario_group import ScenarioGroup

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Scenarios"])


@router.get("/", response_class=HTMLResponse)
async def home():
    """Redirect home to scenarios list."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/scenarios", status_code=303)


@router.get("/scenarios", response_class=HTMLResponse)
async def list_scenarios(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Display scenarios filtered by user's group."""
    # Base query: active, non-deleted
    query = (
        select(Scenario)
        .where(Scenario.is_active == 1)
        .where(Scenario.deleted_at.is_(None))
    )

    # Admin sees all scenarios
    if user.role != "admin":
        if user.group_id:
            # Filter by user's group via scenario_group
            query = query.where(
                Scenario.id.in_(
                    select(ScenarioGroup.scenario_id).where(
                        ScenarioGroup.group_id == user.group_id
                    )
                )
            )
        else:
            # User without group sees no scenarios
            query = query.where(Scenario.id < 0)

    result = await db.execute(query)
    scenarios = result.scalars().all()

    return templates.TemplateResponse(
        "scenarios.html",
        {
            "request": request,
            "user": user,
            "scenarios": scenarios,
        },
    )


@router.get(
    "/scenarios/{scenario_id}",
    response_class=HTMLResponse,
)
async def get_scenario_detail(
    request: Request,
    scenario_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Display scenario and dialogue interface."""
    scenario = await validate_scenario_access(scenario_id, user, db)
    await db.refresh(scenario, ["framework"])

    # Check for existing active session (dedup)
    existing_result = await db.execute(
        select(Session).where(
            Session.scenario_id == scenario.id,
            Session.teacher_id == user.id,
            Session.ended_at.is_(None),
            Session.deleted_at.is_(None),
        )
    )
    session = existing_result.scalars().first()

    if not session:
        session = Session(
            scenario_id=scenario.id,
            teacher_id=user.id,
        )
        db.add(session)

        try:
            await db.flush()
            await db.refresh(session)
            logger.info(
                f"Auto-created session {session.id} "
                f"for user {user.id} "
                f"on scenario {scenario.id}"
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Failed to create session: {e}"
            )
            raise HTTPException(
                status_code=500,
                detail="대화 세션을 시작할 수 없습니다. "
                "잠시 후 다시 시도해주세요.",
            )
    else:
        logger.info(
            f"Reusing session {session.id} "
            f"for user {user.id} "
            f"on scenario {scenario.id}"
        )

    # Load existing messages for the session
    from src.models import Message
    messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session.id)
        .order_by(Message.created_at)
    )
    existing_messages = messages_result.scalars().all()

    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "user": user,
            "scenario": scenario,
            "session_id": session.id,
            "messages": existing_messages,
            "student_name": scenario.student_name,
        },
    )

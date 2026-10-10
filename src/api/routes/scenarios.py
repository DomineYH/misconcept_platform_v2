"""Scenario browsing and selection routes."""

from fastapi import (
    APIRouter,
    Depends,
    Request,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.models import Scenario, User
from src.models.scenario_group import ScenarioGroup
from src.services.lesson_snapshots import (
    public_lesson,
    public_scenario,
    read_lesson_snapshot,
    start_lesson,
)

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
        .where(Scenario.status == "published")
        .where(Scenario.config_json.is_not(None))
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
            "scenarios": [public_scenario(scenario) for scenario in scenarios],
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
    session = await start_lesson(db, scenario_id, user, reuse=True)
    snapshot = read_lesson_snapshot(session)
    scenario = public_lesson(snapshot.scenario_context, snapshot.config)

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
            "session_ended": session.ended_at is not None,
            "messages": existing_messages,
            "student_name": scenario["student_name"],
        },
    )

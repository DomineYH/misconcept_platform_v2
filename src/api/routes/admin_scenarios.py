"""Admin scenario management routes (T077-T080)."""

import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.scenario_input import ScenarioRoute
from src.api.schemas.scenario_config import (
    DraftCreate,
    DraftSaved,
    DraftUpdate,
    RevisionInput,
    ScenarioConfig,
)
from src.models.scenario import Scenario
from src.models.scenario_group import ScenarioGroup
from src.models.session import Session
from src.models.user import User
from src.models.user_group import UserGroup
from src.services.comparison_state import owned_comparison
from src.services.lesson_snapshots import PUBLIC_LESSON_FIELDS
from src.services.scenario_drafts import (
    delete_draft,
    draft_view,
    native_scenario,
    save_scenario,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Scenarios"], route_class=ScenarioRoute)


@router.get("/admin/scenarios", response_class=HTMLResponse)
async def list_all_scenarios(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/scenarios - List all scenarios (T077)."""

    query = (
        select(Scenario)
        .where(Scenario.deleted_at.is_(None))
        .order_by(Scenario.id.desc())
    )
    result = await db.execute(query)
    scenarios = result.scalars().all()

    session_counts = dict(
        (
            await db.execute(
                select(Session.scenario_id, func.count(Session.id)).group_by(
                    Session.scenario_id
                )
            )
        ).all()
    )

    # Load group names for the list
    groups_result = await db.execute(select(UserGroup).order_by(UserGroup.name))
    groups = groups_result.scalars().all()

    # Load scenario-group assignments
    sg_result = await db.execute(select(ScenarioGroup))
    all_sg = sg_result.scalars().all()
    scenario_group_map = {}
    for sg in all_sg:
        scenario_group_map.setdefault(sg.scenario_id, []).append(sg.group_id)

    return templates.TemplateResponse(
        "admin/scenarios.html",
        {
            "request": request,
            "user": user,
            "scenarios": scenarios,
            "session_counts": session_counts,
            "groups": groups,
            "scenario_group_map": scenario_group_map,
        },
    )


@router.post(
    "/admin/scenarios",
    response_model=DraftSaved,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    scenario_data: DraftCreate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios - Create new scenario (T078)."""

    return await save_scenario(db, scenario_data, user.id)


@router.post(
    "/admin/scenarios/{scenario_id}/update",
    response_model=DraftSaved,
)
async def update_scenario(
    scenario_id: int,
    scenario_data: DraftUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios/{id}/update - Update scenario (T079, T080)."""

    return await save_scenario(db, scenario_data, user.id, scenario_id)


@router.get("/admin/scenarios/new", response_class=HTMLResponse)
async def new_scenario_editor(
    request: Request,
    from_comparison: UUID | None = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    editor = dict(
        id=None,
        title="",
        subject="",
        target_grade="",
        is_active=True,
        groups=[],
        config_schema_version=1,
        config_version=1,
        status="draft",
        review_required=False,
        review_reasons=[],
        config=ScenarioConfig(
            problem={}, student={}, mentor={}, analysis={}, runtime={}
        ).model_dump(),
    )
    if from_comparison is not None:
        comparison = await owned_comparison(db, str(from_comparison), user.id)
        source = comparison.source_snapshot
        editor.update(
            {
                key: source[key]
                for key in (
                    "title",
                    "subject",
                    "target_grade",
                    "is_active",
                    "groups",
                    "config_schema_version",
                    "config",
                )
            }
        )
        editor["comparison_draft_id"] = comparison.id
    response = await render_editor(request, user, db, editor)
    if from_comparison is not None:
        response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin/scenarios/{scenario_id}")
async def get_scenario_draft(
    scenario_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    return await draft_view(db, await native_scenario(db, scenario_id))


@router.get("/admin/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
async def edit_scenario_draft(
    request: Request,
    scenario_id: int,
    conversation: UUID | None = None,
    panel: str | None = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    editor = await draft_view(db, await native_scenario(db, scenario_id))
    if conversation is not None:
        from fastapi import HTTPException

        from src.services.comparison_conversations import (
            conversation_model,
            owned_conversation,
        )

        parent = await owned_conversation(db, str(conversation), user.id)
        handoff = await conversation_model(db, parent, panel)
        if handoff["scenario_id"] != scenario_id:
            raise HTTPException(404, detail={"code": "scenario_not_found"})
        editor["model_handoff"] = handoff
    response = await render_editor(request, user, db, editor)
    response.headers["Cache-Control"] = "no-store"
    return response


async def render_editor(request, user, db, editor):
    state, choices = await editor_models(db)
    editor["model_choices"] = choices
    editor["role_defaults"] = (
        state["settings"]["defaults"] if state["settings"] else {}
    )
    editor["available_groups"] = [
        dict(id=g.id, name=g.name)
        for g in (
            await db.scalars(select(UserGroup).order_by(UserGroup.name))
        ).all()
    ]
    editor["public_lesson_fields"] = PUBLIC_LESSON_FIELDS
    editor["publication_available"] = True
    return templates.TemplateResponse(
        "admin/scenario_editor.html",
        dict(request=request, user=user, editor=editor),
    )


@router.post("/admin/scenarios/{scenario_id}/delete")
async def delete_scenario(
    scenario_id: int,
    data: RevisionInput,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """DELETE /admin/scenarios/{id} - Soft delete scenario and sessions.

    Policy: Soft delete all related sessions along with the scenario.
    """

    return await delete_draft(
        db, scenario_id, data.expected_version, user.id, logger
    )


async def editor_models(db):
    from src.models.provider_connection import ProviderConnection
    from src.services.model_configuration import configuration_state
    from src.services.model_verification import connection_ready

    connections = {
        c.provider: c
        for c in (await db.scalars(select(ProviderConnection))).all()
    }
    state = await configuration_state(db, connections)
    choices = [
        dict(
            model,
            provider_connection_id=connections[model["provider"]].id,
            connection_available=connection_ready(
                connections[model["provider"]]
            ),
        )
        for model in state["models"]
    ]
    return state, choices

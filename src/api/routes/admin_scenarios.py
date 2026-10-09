"""Admin scenario management routes (T077-T080)."""

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import Discriminator, Tag
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.scenario_input import ScenarioRoute, input_kind
from src.api.schemas import (
    AdminScenarioResponse,
    ScenarioCreate,
    ScenarioUpdate,
)
from src.api.schemas.scenario_config import (
    DraftCreate,
    DraftSaved,
    DraftUpdate,
    RevisionInput,
    ScenarioConfig,
)
from src.models.analysis_framework import AnalysisFramework
from src.models.prompt_template import PromptTemplate
from src.models.scenario import Scenario
from src.models.scenario_group import ScenarioGroup
from src.models.session import Session
from src.models.user import User
from src.models.user_group import UserGroup
from src.services.admin_scenario_ops import (
    soft_delete_scenario_record,
    update_scenario_record,
)
from src.services.scenario_drafts import (
    delete_draft,
    draft_view,
    field_error,
    native_scenario,
    save_scenario,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Scenarios"], route_class=ScenarioRoute)

CreateInput = Annotated[
    Annotated[DraftCreate, Tag("draft")]
    | Annotated[ScenarioCreate, Tag("legacy")],
    Discriminator(input_kind),
]
UpdateInput = Annotated[
    Annotated[DraftUpdate, Tag("draft")]
    | Annotated[ScenarioUpdate, Tag("legacy")],
    Discriminator(input_kind),
]


@router.get("/admin/scenarios", response_class=HTMLResponse)
async def list_all_scenarios(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/scenarios - List all scenarios (T077)."""

    query = (
        select(Scenario)
        .outerjoin(AnalysisFramework)
        .where(Scenario.deleted_at.is_(None))
        .order_by(Scenario.id.desc())
    )
    result = await db.execute(query)
    scenarios = result.scalars().all()

    # Load frameworks for dropdown
    frameworks_query = select(AnalysisFramework).order_by(
        AnalysisFramework.name
    )
    frameworks_result = await db.execute(frameworks_query)
    frameworks = frameworks_result.scalars().all()

    # Load prompt templates for dropdowns
    student_templates_query = (
        select(PromptTemplate)
        .where(PromptTemplate.bot_type == "student")
        .order_by(PromptTemplate.template_name)
    )
    student_templates_result = await db.execute(student_templates_query)
    student_templates = student_templates_result.scalars().all()

    tutor_templates_query = (
        select(PromptTemplate)
        .where(PromptTemplate.bot_type == "tutor")
        .order_by(PromptTemplate.template_name)
    )
    tutor_templates_result = await db.execute(tutor_templates_query)
    tutor_templates = tutor_templates_result.scalars().all()

    # Get session counts for each scenario
    session_counts = {}
    for scenario in scenarios:
        count_query = select(func.count(Session.id)).where(
            Session.scenario_id == scenario.id
        )
        count = await db.scalar(count_query)
        session_counts[scenario.id] = count or 0

    # Load groups for assignment checkboxes
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
            "frameworks": frameworks,
            "session_counts": session_counts,
            "student_templates": student_templates,
            "tutor_templates": tutor_templates,
            "groups": groups,
            "scenario_group_map": scenario_group_map,
        },
    )


@router.post(
    "/admin/scenarios",
    response_model=DraftSaved | AdminScenarioResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    scenario_data: CreateInput,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios - Create new scenario (T078)."""

    if isinstance(scenario_data, DraftCreate):
        return await save_scenario(db, scenario_data, user.id)

    # Verify framework exists
    framework = await db.get(AnalysisFramework, scenario_data.framework_id)
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Framework not found",
        )

    # Verify student template exists and is correct type
    student_template = await db.get(
        PromptTemplate, scenario_data.student_template_id
    )
    if not student_template or student_template.bot_type != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid student template",
        )

    # Verify tutor template if provided
    if scenario_data.tutor_template_id is not None:
        tutor_template = await db.get(
            PromptTemplate, scenario_data.tutor_template_id
        )
        if not tutor_template or tutor_template.bot_type != "tutor":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tutor template",
            )

    # Create scenario with bot configuration overrides
    scenario = Scenario(
        title=scenario_data.title,
        prompt=scenario_data.prompt,
        student_profile=scenario_data.student_profile,
        framework_id=scenario_data.framework_id,
        is_active=1 if scenario_data.is_active else 0,
        student_name=scenario_data.student_name,
        subject=scenario_data.subject,
        # Phase 2: Bot configuration overrides
        chat_model=scenario_data.chat_model,
        chat_temperature=scenario_data.chat_temperature,
        tutor_intervention_threshold=(
            scenario_data.tutor_intervention_threshold
        ),
        tutor_sensitivity=scenario_data.tutor_sensitivity,
        # Template selections
        student_template_id=scenario_data.student_template_id,
        tutor_template_id=scenario_data.tutor_template_id,
        # Problem situation for preservice teachers
        problem_situation=scenario_data.problem_situation,
        # Greeting message for mentor introduction
        greeting_message=scenario_data.greeting_message,
    )

    db.add(scenario)
    await db.flush()

    # Handle group assignments
    if scenario_data.group_ids:
        for gid in scenario_data.group_ids:
            sg = ScenarioGroup(scenario_id=scenario.id, group_id=gid)
            db.add(sg)

    await db.flush()
    await db.refresh(scenario)

    return scenario


@router.post(
    "/admin/scenarios/{scenario_id}/update",
    response_model=DraftSaved | AdminScenarioResponse,
)
async def update_scenario(
    scenario_id: int,
    scenario_data: UpdateInput,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios/{id}/update - Update scenario (T079, T080)."""

    if isinstance(scenario_data, DraftUpdate):
        return await save_scenario(db, scenario_data, user.id, scenario_id)
    scenario = await db.get(Scenario, scenario_id)
    if scenario and scenario.config_json is not None:
        raise field_error("expected_version", "native_input_required")

    return await update_scenario_record(db, scenario_id, scenario_data, logger)


@router.get("/admin/scenarios/new", response_class=HTMLResponse)
async def new_scenario_editor(
    request: Request,
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
    return await render_editor(request, user, db, editor)


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
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    return await render_editor(
        request,
        user,
        db,
        await draft_view(db, await native_scenario(db, scenario_id)),
    )


async def render_editor(request, user, db, editor):
    from src.models.provider_connection import ProviderConnection
    from src.services.model_configuration import configuration_state
    from src.services.model_verification import connection_ready

    connections = {
        c.provider: c
        for c in (await db.scalars(select(ProviderConnection))).all()
    }
    state = await configuration_state(db, connections)
    editor["model_choices"] = [
        dict(
            model,
            provider_connection_id=connections[model["provider"]].id,
            connection_available=connection_ready(
                connections[model["provider"]]
            ),
        )
        for model in state["models"]
    ]
    editor["role_defaults"] = (
        state["settings"]["defaults"] if state["settings"] else {}
    )
    editor["available_groups"] = [
        dict(id=g.id, name=g.name)
        for g in (
            await db.scalars(select(UserGroup).order_by(UserGroup.name))
        ).all()
    ]
    editor["publication_available"] = True
    return templates.TemplateResponse(
        "admin/scenario_editor.html",
        dict(request=request, user=user, editor=editor),
    )


@router.post("/admin/scenarios/{scenario_id}/delete")
async def delete_scenario(
    scenario_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    data: RevisionInput | None = None,
):
    """DELETE /admin/scenarios/{id} - Soft delete scenario and sessions.

    Policy: Soft delete all related sessions along with the scenario.
    """

    scenario = await db.get(Scenario, scenario_id)
    if scenario and scenario.config_json is not None:
        if data is None:
            raise field_error("expected_version", "version_required")
        return await delete_draft(
            db, scenario_id, data.expected_version, user.id, logger
        )

    return await soft_delete_scenario_record(db, scenario_id, user.id, logger)

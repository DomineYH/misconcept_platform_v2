"""Admin scenario management routes (T077-T080)."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.schemas import (
    AdminScenarioResponse,
    ScenarioCreate,
    ScenarioUpdate,
)
from src.models.analysis_framework import AnalysisFramework
from src.models.prompt_template import PromptTemplate
from src.models.scenario import Scenario
from src.models.scenario_group import ScenarioGroup
from src.models.session import Session
from src.models.user import User
from src.models.user_group import UserGroup
from src.services.admin_scenario_ops import update_scenario_record, soft_delete_scenario_record

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Scenarios"])


@router.get("/admin/scenarios", response_class=HTMLResponse)
async def list_all_scenarios(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/scenarios - List all scenarios (T077)."""

    query = (
        select(Scenario)
        .join(AnalysisFramework)
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
    response_model=AdminScenarioResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    scenario_data: ScenarioCreate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios - Create new scenario (T078)."""

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
        # Video fields
        video_url=scenario_data.video_url,
        video_transcript=scenario_data.video_transcript,
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
    response_model=AdminScenarioResponse,
)
async def update_scenario(
    scenario_id: int,
    scenario_data: ScenarioUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/scenarios/{id}/update - Update scenario (T079, T080)."""

    return await update_scenario_record(db, scenario_id, scenario_data, logger)


@router.post("/admin/scenarios/{scenario_id}/delete")
async def delete_scenario(
    scenario_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """DELETE /admin/scenarios/{id} - Soft delete scenario and sessions.

    Policy: Soft delete all related sessions along with the scenario.
    """

    return await soft_delete_scenario_record(db, scenario_id, user.id, logger)

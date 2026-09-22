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

    # Get scenario
    scenario = await db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )

    # Log warning if active sessions exist (T080)
    active_sessions_query = (
        select(func.count(Session.id))
        .where(Session.scenario_id == scenario_id)
        .where(Session.ended_at.is_(None))
    )
    active_sessions_count = await db.scalar(active_sessions_query)

    if active_sessions_count and active_sessions_count > 0:
        logger.warning(
            "Scenario %d updated with %d active sessions",
            scenario_id,
            active_sessions_count,
        )

    # Update fields
    if scenario_data.title is not None:
        scenario.title = scenario_data.title.strip()
    if scenario_data.prompt is not None:
        scenario.prompt = scenario_data.prompt.strip()
    if scenario_data.student_profile is not None:
        scenario.student_profile = scenario_data.student_profile.strip()
    if scenario_data.problem_situation is not None:
        scenario.problem_situation = (
            scenario_data.problem_situation.strip()
            if scenario_data.problem_situation
            else None
        )
    if scenario_data.greeting_message is not None:
        scenario.greeting_message = (
            scenario_data.greeting_message.strip()
            if scenario_data.greeting_message
            else None
        )
    if scenario_data.student_name is not None:
        scenario.student_name = (
            scenario_data.student_name.strip()
            if scenario_data.student_name
            else None
        )
    if scenario_data.subject is not None:
        scenario.subject = (
            scenario_data.subject.strip() if scenario_data.subject else None
        )
    if scenario_data.framework_id is not None:
        # Verify framework exists
        framework = await db.get(AnalysisFramework, scenario_data.framework_id)
        if not framework:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Framework not found",
            )
        scenario.framework_id = scenario_data.framework_id
    if scenario_data.is_active is not None:
        scenario.is_active = scenario_data.is_active

    # Phase 2: Update bot configuration overrides
    if scenario_data.chat_model is not None:
        scenario.chat_model = scenario_data.chat_model
    if scenario_data.chat_temperature is not None:
        scenario.chat_temperature = scenario_data.chat_temperature
    if scenario_data.tutor_intervention_threshold is not None:
        scenario.tutor_intervention_threshold = (
            scenario_data.tutor_intervention_threshold
        )
    if scenario_data.tutor_sensitivity is not None:
        scenario.tutor_sensitivity = scenario_data.tutor_sensitivity

    # Update template selections
    if scenario_data.student_template_id is not None:
        student_template = await db.get(
            PromptTemplate, scenario_data.student_template_id
        )
        if not student_template or student_template.bot_type != "student":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid student template",
            )
        scenario.student_template_id = scenario_data.student_template_id

    if scenario_data.tutor_template_id is not None:
        # Special value handling: if -1, set to None (disable tutor)
        if scenario_data.tutor_template_id == -1:
            scenario.tutor_template_id = None
        else:
            tutor_template = await db.get(
                PromptTemplate, scenario_data.tutor_template_id
            )
            if not tutor_template or tutor_template.bot_type != "tutor":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid tutor template",
                )
            scenario.tutor_template_id = scenario_data.tutor_template_id

    # Update video fields
    if scenario_data.video_url is not None:
        scenario.video_url = scenario_data.video_url
    if scenario_data.video_transcript is not None:
        scenario.video_transcript = scenario_data.video_transcript

    # Update group assignments
    if scenario_data.group_ids is not None:
        # Delete old assignments
        old_sgs = await db.execute(
            select(ScenarioGroup).where(
                ScenarioGroup.scenario_id == scenario_id
            )
        )
        for sg in old_sgs.scalars().all():
            await db.delete(sg)
        await db.flush()  # flush deletes before inserts
        # Insert new assignments
        for gid in scenario_data.group_ids:
            sg = ScenarioGroup(scenario_id=scenario_id, group_id=gid)
            db.add(sg)

    await db.flush()
    await db.refresh(scenario)

    return scenario


@router.post("/admin/scenarios/{scenario_id}/delete")
async def delete_scenario(
    scenario_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """DELETE /admin/scenarios/{id} - Soft delete scenario and sessions.

    Policy: Soft delete all related sessions along with the scenario.
    """

    # Load scenario (only active, not deleted)
    query = select(Scenario).where(
        Scenario.id == scenario_id, Scenario.deleted_at.is_(None)
    )
    result = await db.execute(query)
    scenario = result.scalar_one_or_none()

    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found or already deleted",
        )

    # Get all non-deleted sessions for this scenario
    sessions_query = select(Session).where(
        Session.scenario_id == scenario_id, Session.deleted_at.is_(None)
    )
    sessions_result = await db.execute(sessions_query)
    sessions = sessions_result.scalars().all()

    # Soft delete all sessions
    for session in sessions:
        session.mark_deleted()

    # Soft delete the scenario
    scenario.mark_deleted()

    # Commit all changes
    await db.flush()

    logger.info(
        f"Scenario {scenario_id} and {len(sessions)} related session(s) "
        f"soft-deleted by user {user.id}"
    )

    return {"status": "deleted", "scenario_id": scenario_id}

"""Shared admin scenario operations."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import ScenarioUpdate
from src.models.analysis_framework import AnalysisFramework
from src.models.prompt_template import PromptTemplate
from src.models.scenario import Scenario
from src.models.scenario_group import ScenarioGroup
from src.models.session import Session


async def update_scenario_record(
    db: AsyncSession,
    scenario_id: int,
    scenario_data: ScenarioUpdate,
    logger: logging.Logger,
) -> Scenario:
    """Update a scenario and its group assignments."""
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


async def soft_delete_scenario_record(
    db: AsyncSession,
    scenario_id: int,
    user_id: int,
    logger: logging.Logger,
) -> dict[str, int | str]:
    """Soft-delete a scenario and all related sessions."""
    query = select(Scenario).where(
        Scenario.id == scenario_id,
        Scenario.deleted_at.is_(None),
    )
    result = await db.execute(query)
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found or already deleted",
        )

    sessions_query = select(Session).where(
        Session.scenario_id == scenario_id,
        Session.deleted_at.is_(None),
    )
    sessions_result = await db.execute(sessions_query)
    sessions = sessions_result.scalars().all()
    for session in sessions:
        session.mark_deleted()

    scenario.mark_deleted()
    await db.flush()

    logger.info(
        "Scenario %d and %d related session(s) soft-deleted by user %d",
        scenario_id,
        len(sessions),
        user_id,
    )
    return {"status": "deleted", "scenario_id": scenario_id}

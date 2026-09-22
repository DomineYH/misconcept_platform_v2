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
    scenario = await db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )

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

    provided = scenario_data.model_fields_set

    if "title" in provided and scenario_data.title is not None:
        scenario.title = scenario_data.title.strip()
    if "prompt" in provided and scenario_data.prompt is not None:
        scenario.prompt = scenario_data.prompt.strip()
    if (
        "student_profile" in provided
        and scenario_data.student_profile is not None
    ):
        scenario.student_profile = scenario_data.student_profile.strip()
    if "student_name" in provided:
        scenario.student_name = _strip_or_none(scenario_data.student_name)
    if "subject" in provided:
        scenario.subject = _strip_or_none(scenario_data.subject)
    if "framework_id" in provided and scenario_data.framework_id is not None:
        framework = await db.get(
            AnalysisFramework,
            scenario_data.framework_id,
        )
        if not framework:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Framework not found",
            )
        scenario.framework_id = scenario_data.framework_id
    if "is_active" in provided and scenario_data.is_active is not None:
        scenario.is_active = scenario_data.is_active

    if "chat_model" in provided:
        scenario.chat_model = _strip_or_none(scenario_data.chat_model)
    if "chat_temperature" in provided:
        scenario.chat_temperature = scenario_data.chat_temperature
    if "tutor_intervention_threshold" in provided:
        scenario.tutor_intervention_threshold = (
            scenario_data.tutor_intervention_threshold
        )
    if "tutor_sensitivity" in provided:
        scenario.tutor_sensitivity = scenario_data.tutor_sensitivity or "medium"

    if "student_template_id" in provided:
        if scenario_data.student_template_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid student template",
            )
        student_template = await db.get(
            PromptTemplate,
            scenario_data.student_template_id,
        )
        if not student_template or student_template.bot_type != "student":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid student template",
            )
        scenario.student_template_id = scenario_data.student_template_id

    if "tutor_template_id" in provided:
        tutor_template_id = scenario_data.tutor_template_id
        if tutor_template_id in (None, -1):
            scenario.tutor_template_id = None
        else:
            tutor_template = await db.get(PromptTemplate, tutor_template_id)
            if not tutor_template or tutor_template.bot_type != "tutor":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid tutor template",
                )
            scenario.tutor_template_id = tutor_template_id

    if "video_url" in provided:
        scenario.video_url = _strip_or_none(scenario_data.video_url)
    if "video_transcript" in provided:
        scenario.video_transcript = _strip_or_none(
            scenario_data.video_transcript
        )

    if "group_ids" in provided and scenario_data.group_ids is not None:
        old_sgs = await db.execute(
            select(ScenarioGroup).where(
                ScenarioGroup.scenario_id == scenario_id
            )
        )
        for scenario_group in old_sgs.scalars().all():
            await db.delete(scenario_group)
        await db.flush()
        for group_id in scenario_data.group_ids:
            db.add(
                ScenarioGroup(
                    scenario_id=scenario_id,
                    group_id=group_id,
                )
            )

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


def _strip_or_none(value: str | None) -> str | None:
    """Trim a string and convert blanks to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None

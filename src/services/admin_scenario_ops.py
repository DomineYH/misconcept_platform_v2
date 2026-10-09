"""Shared admin scenario operations."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.scenario import Scenario
from src.models.session import Session


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

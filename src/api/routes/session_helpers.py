"""Shared helper functions for session management."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Scenario, Session, User
from src.models.scenario_group import ScenarioGroup


async def load_session(
    session_id: int,
    user: User,
    db: AsyncSession,
) -> Session:
    """Load session and validate ownership.

    Args:
        session_id: Session ID to load
        user: Current user from auth
        db: Database session

    Returns:
        Session object

    Raises:
        HTTPException: 404 if not found or wrong owner, 403 if forbidden
    """
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.deleted_at.is_(None)
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.teacher_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return session


async def mark_session_ended(
    session: Session,
    db: AsyncSession,
    *,
    force: bool = False,
) -> tuple[datetime, bool]:
    """Mark session as ended by setting ended_at timestamp.

    Args:
        session: Session to mark as ended
        db: Database session
        force: If True, allows idempotent behavior (no error if already ended)

    Returns:
        Tuple of (ended_at timestamp, was_already_ended boolean)

    Raises:
        HTTPException: 400 if already ended and force=False
    """
    if session.ended_at:
        if not force:
            raise HTTPException(status_code=400, detail="Session already ended")
        return session.ended_at, True

    session.ended_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(session)

    return session.ended_at, False


async def validate_scenario_access(
    scenario_id: int,
    user: User,
    db: AsyncSession,
) -> Scenario:
    """Validate user can access the given scenario.

    Checks: existence, soft-delete, active status, and
    group-based access control. Admins bypass group checks.

    Returns:
        Scenario object if access is granted.

    Raises:
        HTTPException: 404 if not found/inactive, 403 if no access.
    """
    result = await db.execute(
        select(Scenario).where(
            Scenario.id == scenario_id,
            Scenario.deleted_at.is_(None),
        )
    )
    scenario = result.scalar_one_or_none()

    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    if scenario.is_active != 1 and not user.is_admin:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Admin bypasses group check
    if user.is_admin:
        return scenario

    if not user.group_id:
        raise HTTPException(
            status_code=403,
            detail="그룹이 배정되지 않았습니다.",
        )

    sg = await db.execute(
        select(ScenarioGroup).where(
            ScenarioGroup.scenario_id == scenario_id,
            ScenarioGroup.group_id == user.group_id,
        )
    )
    if not sg.scalar_one_or_none():
        raise HTTPException(
            status_code=403,
            detail="이 시나리오에 대한 접근 권한이 " "없습니다.",
        )

    return scenario

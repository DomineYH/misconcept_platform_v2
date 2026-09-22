"""Admin session listing and statistics routes."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.models.session import Session
from src.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


def safe_int(value: Optional[str]) -> Optional[int]:
    """Convert string to int, returning None for empty or invalid values."""
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_date_filter(
    date_str: Optional[str], field_name: str
) -> Tuple[Optional[datetime], Optional[str]]:
    """Parse date filter string to datetime.

    Args:
        date_str: ISO format date string (optional)
        field_name: Name of field for error message

    Returns:
        Tuple of (parsed datetime or None, error or None)
    """
    if not date_str:
        return None, None
    try:
        return (
            datetime.fromisoformat(date_str.replace("Z", "")),
            None,
        )
    except ValueError:
        return (
            None,
            f"Invalid {field_name} format: '{date_str}'",
        )


@router.get("/admin/sessions-page", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    teacher_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Render session listing page with filtering."""
    per_page = 10
    offset = (page - 1) * per_page

    teacher_id_val = safe_int(teacher_id)

    base_query = select(Session).where(Session.deleted_at.is_(None))

    if teacher_id_val:
        base_query = base_query.where(Session.teacher_id == teacher_id_val)

    # Parse date filters with validation
    date_errors = []
    if date_from:
        dt_from, err = parse_date_filter(date_from, "date_from")
        if err:
            date_errors.append(err)
        elif dt_from:
            base_query = base_query.where(Session.started_at >= dt_from)
    if date_to:
        dt_to, err = parse_date_filter(date_to, "date_to")
        if err:
            date_errors.append(err)
        elif dt_to:
            base_query = base_query.where(Session.started_at <= dt_to)

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total_count = count_result.scalar() or 0
    total_pages = (total_count + per_page - 1) // per_page

    query = (
        base_query.options(
            joinedload(Session.scenario),
            joinedload(Session.teacher),
            joinedload(Session.summary),
        )
        .order_by(desc(Session.started_at))
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    teachers_result = await db.execute(
        select(User)
        .where(User.id.in_(select(Session.teacher_id).distinct()))
        .order_by(User.nickname)
    )
    teachers = teachers_result.scalars().all()

    return templates.TemplateResponse(
        "admin/sessions.html",
        {
            "request": request,
            "user": user,
            "sessions": sessions,
            "teachers": teachers,
            "current_teacher_id": teacher_id_val,
            "current_date_from": date_from or "",
            "current_date_to": date_to or "",
            "current_page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "date_errors": date_errors,
        },
    )


@router.get("/admin/sessions", response_class=JSONResponse)
async def list_sessions_api(
    scenario_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, List[Dict[str, Any]]]:
    """List all sessions with optional filtering (API)."""
    scenario_id_val = safe_int(scenario_id)
    teacher_id_val = safe_int(teacher_id)

    query = (
        select(Session)
        .options(
            joinedload(Session.scenario),
            joinedload(Session.teacher),
        )
        .where(Session.deleted_at.is_(None))
        .order_by(desc(Session.started_at))
    )

    if scenario_id_val:
        query = query.where(Session.scenario_id == scenario_id_val)

    if teacher_id_val:
        query = query.where(Session.teacher_id == teacher_id_val)

    if date_from:
        dt, err = parse_date_filter(date_from, "date_from")
        if err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )
        if dt:
            query = query.where(Session.started_at >= dt)

    if date_to:
        dt, err = parse_date_filter(date_to, "date_to")
        if err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )
        if dt:
            query = query.where(Session.started_at <= dt)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return {
        "sessions": [
            {
                "id": s.id,
                "scenario_id": s.scenario_id,
                "scenario_title": (
                    s.scenario.title if s.scenario else "Unknown"
                ),
                "teacher_id": s.teacher_id,
                "teacher_nickname": (
                    s.teacher.nickname if s.teacher else "Unknown"
                ),
                "started_at": s.started_at.isoformat(),
                "ended_at": (s.ended_at.isoformat() if s.ended_at else None),
                "status": ("ended" if s.ended_at else "active"),
            }
            for s in sessions
        ]
    }

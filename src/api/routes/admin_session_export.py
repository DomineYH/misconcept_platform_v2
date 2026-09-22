"""Admin session CSV export routes."""

import logging
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    status,
)
from fastapi.responses import Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session
from src.api.routes.admin_sessions import (
    parse_date_filter,
    safe_int,
)
from src.models.message import Message
from src.models.scenario import Scenario
from src.models.session import Session
from src.models.user import User
from src.services.export import CSVExporter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/sessions/export")
async def export_sessions(
    scenario_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Export filtered sessions as CSV (ended only)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    scenario_id_val = safe_int(scenario_id)
    teacher_id_val = safe_int(teacher_id)

    query = (
        select(Session.id)
        .where(Session.deleted_at.is_(None))
        .where(Session.ended_at.isnot(None))
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
    session_ids = result.scalars().all()

    if not session_ids:
        return Response(
            content="No sessions found",
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(
        list(session_ids), db
    )

    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": ("attachment; filename=sessions_export.csv")
        },
    )


@router.post("/admin/sessions/export-selected")
async def export_selected_sessions(
    session_ids: List[int] = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Export selected sessions as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    if not session_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No session_ids provided",
        )

    result = await db.execute(
        select(Session).where(Session.id.in_(session_ids))
    )
    sessions = result.scalars().all()

    for s in sessions:
        if s.ended_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Session {s.id} is still active." " Only ended allowed."
                ),
            )

    valid_ids = [s.id for s in sessions if s.deleted_at is None]
    if not valid_ids:
        return Response(
            content="No valid sessions",
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(valid_ids, db)

    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment;" " filename=selected_sessions.csv"
            )
        },
    )


@router.get("/admin/user-conversations/{user_id}/export")
async def export_user_conversations(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Download all conversations for a user as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    query = (
        select(Session.id)
        .where(
            Session.teacher_id == user_id,
            Session.deleted_at.is_(None),
            Session.ended_at.isnot(None),
        )
        .order_by(desc(Session.started_at))
    )
    result = await db.execute(query)
    session_ids = result.scalars().all()

    if not session_ids:
        return Response(
            content="No completed sessions found",
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(
        list(session_ids), db
    )

    filename = f"user_{user_id}_conversations.csv"
    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": (f"attachment; filename={filename}")},
    )


@router.get("/admin/user-conversations/{user_id}/sessions")
async def list_user_sessions(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List completed sessions for individual download."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    query = (
        select(
            Session.id,
            Session.started_at,
            Scenario.title.label("scenario_title"),
            func.count(Message.id).label("message_count"),
        )
        .join(
            Scenario,
            Session.scenario_id == Scenario.id,
        )
        .outerjoin(
            Message,
            Message.session_id == Session.id,
        )
        .where(
            Session.teacher_id == user_id,
            Session.deleted_at.is_(None),
            Session.ended_at.isnot(None),
        )
        .group_by(Session.id, Scenario.title)
        .order_by(desc(Session.started_at))
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "id": row.id,
            "started_at": row.started_at.strftime("%Y-%m-%d %H:%M"),
            "scenario_title": row.scenario_title,
            "message_count": row.message_count,
        }
        for row in rows
    ]


@router.get("/admin/sessions/{session_id}/download")
async def download_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Download a single session as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    session = await db.get(Session, session_id)
    if not session or session.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.ended_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot download active session",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_session_admin(session_id, db)

    filename = f"session_{session_id}.csv"
    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": (f"attachment; filename={filename}")},
    )

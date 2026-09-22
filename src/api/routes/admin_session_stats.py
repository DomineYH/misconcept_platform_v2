"""Admin session statistics and user conversation routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.models.session import Session
from src.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/stats")
async def get_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get system statistics for dashboard charts."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    total_teachers_query = select(
        func.count(func.distinct(Session.teacher_id))
    ).where(Session.deleted_at.is_(None))
    total_teachers = (
        await db.scalar(total_teachers_query) or 0
    )

    total_sessions_query = select(
        func.count(Session.id)
    ).where(Session.deleted_at.is_(None))
    total_sessions = (
        await db.scalar(total_sessions_query) or 0
    )

    active_sessions_query = select(
        func.count(Session.id)
    ).where(
        Session.ended_at.is_(None),
        Session.deleted_at.is_(None),
    )
    active_sessions = (
        await db.scalar(active_sessions_query) or 0
    )

    duration_query = (
        select(
            func.avg(
                func.julianday(Session.ended_at)
                - func.julianday(Session.started_at)
            )
            * 24
            * 60
        ).where(
            Session.ended_at.isnot(None),
            Session.deleted_at.is_(None),
        )
    )
    avg_duration = await db.scalar(duration_query) or 0

    return {
        "total_teachers": total_teachers,
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "avg_session_duration_minutes": round(
            avg_duration, 1
        ),
        "avg_questions_per_session": 0,
    }


@router.get(
    "/admin/user-conversations",
    response_class=HTMLResponse,
)
async def user_conversations_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List users with conversation session counts."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    subq_total = (
        select(
            Session.teacher_id,
            func.count(Session.id).label(
                "session_count"
            ),
            func.max(Session.started_at).label(
                "last_session"
            ),
        )
        .where(Session.deleted_at.is_(None))
        .group_by(Session.teacher_id)
        .subquery()
    )

    subq_ended = (
        select(
            Session.teacher_id,
            func.count(Session.id).label("ended_count"),
        )
        .where(
            Session.deleted_at.is_(None),
            Session.ended_at.isnot(None),
        )
        .group_by(Session.teacher_id)
        .subquery()
    )

    query = (
        select(
            User,
            subq_total.c.session_count,
            subq_total.c.last_session,
            func.coalesce(
                subq_ended.c.ended_count, 0
            ).label("ended_count"),
        )
        .join(
            subq_total,
            User.id == subq_total.c.teacher_id,
        )
        .outerjoin(
            subq_ended,
            User.id == subq_ended.c.teacher_id,
        )
        .order_by(desc(subq_total.c.session_count))
    )

    result = await db.execute(query)
    rows = result.all()

    users_data = [
        {
            "user": row[0],
            "session_count": row[1],
            "last_session": row[2],
            "ended_session_count": row[3],
        }
        for row in rows
    ]

    return templates.TemplateResponse(
        "admin/user_conversations.html",
        {
            "request": request,
            "user": user,
            "users": users_data,
        },
    )

"""Admin session CSV export routes."""

import csv
import io
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session
from src.api.routes.admin_session_actions import session_missing
from src.api.routes.admin_sessions import (
    parse_date_filter,
    safe_int,
)
from src.config import config
from src.demo.database import DemoError
from src.i18n import display_language, translate
from src.models.message import Message
from src.models.session import Session
from src.models.user import User
from src.services.export import CSVExporter
from src.services.session_history import session_display

logger = logging.getLogger(__name__)
router = APIRouter()


def demo_seed_session(request):
    """§23 record origin from server seed refs; None keeps normal CSV."""
    if config.APP_MODE != "demo":
        return None
    runtime = getattr(request.app.state, "demo_request_db", None)
    seed = getattr(runtime, "seed_refs", {}).get("completed_session")
    if type(seed) is not int:
        raise DemoError("CONFIGURATION_INVALID")
    return seed


def csv_response(request, csv_content, filename, target, *, echo=False):
    """Normal keeps its filename; demo uses the fixed §23 ASCII name."""
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    if config.APP_MODE == "demo":
        headers["Content-Disposition"] = (
            f"attachment; filename=demo_d0-02-v1_admin_{target}.csv"
        )
        if echo:
            # §24.1 S5: the tour verifies this echo and count before csv_received.
            request_id = request.headers.get("x-demo-export-request-id")
            if request_id is not None:
                try:
                    canonical = str(UUID(request_id)) == request_id
                except ValueError:
                    canonical = False
                if not canonical:
                    raise DemoError("INVALID_REQUEST")
                headers["X-Demo-Export-Request-ID"] = request_id
            rows = len(list(csv.DictReader(io.StringIO(csv_content))))
            # §24.1 requires a count >= 1; never fabricate a row for it.
            if rows < 1:
                raise DemoError("INVALID_REQUEST")
            headers["X-Demo-Export-Row-Count"] = str(rows)
    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get("/admin/sessions/export")
async def export_sessions(
    scenario_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """Export filtered sessions as CSV (ended only)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate("admin.admin_required", display_language(request)),
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
        dt, err = parse_date_filter(
            date_from, "date_from", display_language(request)
        )
        if err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )
        if dt:
            query = query.where(Session.started_at >= dt)
    if date_to:
        dt, err = parse_date_filter(
            date_to, "date_to", display_language(request)
        )
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
            content=translate(
                "admin.sessions_missing", display_language(request)
            ),
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(
        list(session_ids), db, demo_seed_session_id=demo_seed_session(request)
    )

    return csv_response(request, csv_content, "sessions_export.csv", "all")


@router.post("/admin/sessions/export-selected")
async def export_selected_sessions(
    session_ids: List[int] = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """Export selected sessions as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate("admin.admin_required", display_language(request)),
        )

    if not session_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=translate(
                "admin.session_selection_required", display_language(request)
            ),
        )

    result = await db.execute(
        select(Session).where(
            Session.id.in_(session_ids), Session.deleted_at.is_(None)
        )
    )
    sessions = result.scalars().all()

    for s in sessions:
        if s.ended_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    translate(
                        "admin.active_session",
                        display_language(request),
                        session_id=s.id,
                    )
                ),
            )

    valid_ids = [s.id for s in sessions]
    if not valid_ids:
        return Response(
            content=translate(
                "admin.valid_sessions_missing", display_language(request)
            ),
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(
        valid_ids, db, demo_seed_session_id=demo_seed_session(request)
    )

    return csv_response(
        request, csv_content, "selected_sessions.csv", "selected", echo=True
    )


@router.get("/admin/user-conversations/{user_id}/export")
async def export_user_conversations(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """Download all conversations for a user as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate("admin.admin_required", display_language(request)),
        )

    target_user = await db.get(User, user_id)
    if not target_user:
        if config.APP_MODE == "demo":
            raise DemoError("RESOURCE_NOT_FOUND")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=translate("admin.user_missing", display_language(request)),
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
            content=translate(
                "admin.completed_sessions_missing", display_language(request)
            ),
            media_type="text/plain",
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_multiple_sessions_admin(
        list(session_ids), db, demo_seed_session_id=demo_seed_session(request)
    )

    return csv_response(
        request,
        csv_content,
        f"user_{user_id}_conversations.csv",
        f"user-{user_id}",
    )


@router.get("/admin/user-conversations/{user_id}/sessions")
async def list_user_sessions(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """List completed sessions for individual download."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate("admin.admin_required", display_language(request)),
        )

    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=translate("admin.user_missing", display_language(request)),
        )

    query = (
        select(
            Session,
            func.count(Message.id).label("message_count"),
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
        .group_by(Session.id)
        .order_by(desc(Session.started_at))
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "id": session.id,
            "started_at": session.started_at.strftime("%Y-%m-%d %H:%M"),
            **session_display(session),
            "message_count": message_count,
        }
        for session, message_count in rows
    ]


@router.get("/admin/sessions/{session_id}/download")
async def download_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """Download a single session as CSV."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate("admin.admin_required", display_language(request)),
        )

    session = await db.get(Session, session_id)
    if not session or session.deleted_at is not None:
        raise session_missing(request)

    if session.ended_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=translate(
                "admin.active_download", display_language(request)
            ),
        )

    exporter = CSVExporter()
    csv_content = await exporter.export_session_admin(
        session_id, db, demo_seed_session_id=demo_seed_session(request)
    )

    return csv_response(
        request,
        csv_content,
        f"session_{session_id}.csv",
        f"session-{session_id}",
        echo=True,
    )

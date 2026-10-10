"""Admin session action routes."""

from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.session_analysis import AnalysisRequest
from src.api.routes.session_helpers import (
    mark_session_ended,
    require_native_session,
)
from src.config import config
from src.models.session import Session
from src.models.user import User
from src.services.analysis_results import (
    load_analysis_response as _load_analysis_response,
)
from src.services.analysis_runs import request_analysis
from src.services.session_history import session_display

router = APIRouter()
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


@router.post(
    "/admin/sessions/{session_id}/end",
    response_class=HTMLResponse,
)
async def end_session(
    request: Request,
    session_id: int,
    body: AnalysisRequest | None = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """End an active session (set ended_at)."""
    query = (
        select(Session)
        .options(
            joinedload(Session.scenario),
            joinedload(Session.teacher),
        )
        .where(Session.id == session_id)
    )
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    require_native_session(session)
    await mark_session_ended(session, db, force=True)
    accepted = await request_analysis(
        db,
        session_id,
        user.id,
        body.request_id if body else str(uuid4()),
        plan_hash=body.plan_hash if body else None,
    )
    if body is not None:
        return accepted

    # Reload render inputs after committing the end and analysis reservation.
    session = (
        await db.execute(
            query.options(joinedload(Session.summary)).execution_options(
                populate_existing=True
            )
        )
    ).scalar_one()

    return templates.TemplateResponse(
        "partials/session_row.html",
        {
            "request": request,
            "session": session,
            "session_display": session_display,
            "analysis_session_ids": {session_id},
        },
        status_code=accepted.status_code,
    )


@router.post(
    "/admin/sessions/{session_id}/delete",
    response_class=HTMLResponse,
)
async def delete_session(
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Soft delete a session (set deleted_at)."""
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session already deleted",
        )

    session.mark_deleted()
    await db.flush()

    return Response(content="", status_code=200)


@router.get(
    "/admin/sessions/{session_id}/detail",
    response_class=HTMLResponse,
)
async def session_detail(
    request: Request,
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get session detail for viewing in modal."""
    query = (
        select(Session)
        .options(
            joinedload(Session.scenario),
            joinedload(Session.teacher),
            selectinload(Session.messages),
        )
        .where(Session.id == session_id)
    )
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return templates.TemplateResponse(
        "partials/session_detail.html",
        {"request": request, "session": session, **session_display(session)},
    )


@router.get(
    "/admin/sessions/{session_id}/analysis_modal",
    response_class=HTMLResponse,
)
async def analysis_modal(
    request: Request,
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get session analysis for admin modal view."""
    query = (
        select(Session)
        .options(joinedload(Session.scenario))
        .where(Session.id == session_id)
    )
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if not session.ended_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Session must be ended before viewing analysis"),
        )

    analysis_data = await _load_analysis_response(session_id, db, admin=True)
    if analysis_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found",
        )

    return templates.TemplateResponse(
        "partials/analysis_modal.html",
        {
            "request": request,
            "user": user,
            "session_id": session_id,
            "is_admin": True,
            **(
                {
                    "analysis_result_url": f"/admin/sessions/{session_id}/analysis"
                }
                if "accepted_report" in analysis_data
                else {}
            ),
            **analysis_data,
        },
    )


@router.get("/admin/sessions/{session_id}/analysis")
async def get_admin_analysis(
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(404, detail="Session not found")
    if not session.ended_at:
        raise HTTPException(
            400, detail="Session must be ended before viewing analysis"
        )
    result = await _load_analysis_response(session_id, db, admin=True)
    if result is None:
        raise HTTPException(404, detail="Analysis not found")
    return result


@router.post("/admin/sessions/{session_id}/analyze_regenerate")
@limiter.limit("2/minute")
async def regenerate_analysis(
    request: Request,
    body: AnalysisRequest,
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Reserve a new analysis while preserving the accepted report."""
    return await request_analysis(
        db,
        session_id,
        user.id,
        body.request_id,
        regenerate=True,
        plan_hash=body.plan_hash,
    )


@router.get("/admin/sessions/{session_id}/analysis/runs/{run_id}")
async def get_admin_analysis_run(
    session_id: int,
    run_id: str,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.analysis_runs import get_run, run_response

    run = await get_run(db, session_id, run_id)
    session = await db.get(Session, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, detail="Session not found")
    return await run_response(db, run, admin=True)


@router.post(
    "/admin/sessions/{session_id}/analysis/runs/{run_id}/cancel",
    status_code=202,
)
async def cancel_admin_analysis_run(
    session_id: int,
    run_id: str,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.analysis_runs import cancel_analysis, get_run

    await get_run(db, session_id, run_id)
    session = await db.get(Session, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, detail="Session not found")
    return await cancel_analysis(db, session_id, run_id, user.id, admin=True)

"""Session analysis routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.api.routes.session_helpers import (
    load_session,
    mark_session_ended,
    require_native_session,
)
from src.config import config
from src.models import (
    UiEvent,
    User,
)
from src.services.analysis_pipeline import (
    analyze_session,
    handle_analysis_failure,
    handle_duplicate_session_state,
    load_analysis_lesson,
)
from src.services.analysis_results import (
    analysis_status,
    load_analysis_response,
    load_summary,
    summary_response,
)
from src.services.export import CSVExporter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sessions"])
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


@router.post("/sessions/{session_id}/end")
@limiter.limit("10/minute")
async def end_session(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """End session without running analysis.

    This endpoint only marks the session as ended. Use the /analyze
    endpoint to run the analysis separately.
    """
    session = await load_session(session_id, user, db)
    await mark_session_ended(session, db, force=True)

    return {
        "ended": True,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post("/sessions/{session_id}/analyze")
@limiter.limit("5/minute")
async def analyze_session_endpoint(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Analyze questions and generate summary for an ended session."""
    session = await load_session(session_id, user, db)
    require_native_session(session)

    # Session must be ended before analysis
    if not session.ended_at:
        raise HTTPException(
            status_code=400,
            detail="Session must be ended before analysis",
        )

    existing_summary, existing_report = await load_summary(session_id, db)
    if (
        existing_summary
        and analysis_status(existing_summary, existing_report) != "failed"
    ):
        if existing_report is not None and existing_report.version == 2:
            return await load_analysis_response(session_id, db)
        return summary_response(existing_summary, existing_report)

    snapshot = await load_analysis_lesson(db, session_id, user.id)
    label_names = (
        [r.id for r in snapshot.config.analysis.rubric]
        if snapshot.config.analysis.classification_enabled
        else []
    )
    try:
        saved = await analyze_session(session_id, session, db, actor_id=user.id)
        _, report = await load_summary(session_id, db)
        if report is not None and report.version == 2:
            return await load_analysis_response(session_id, db)
        return saved
    except IntegrityError as e:
        return await handle_duplicate_session_state(
            session_id, label_names, db, e
        )
    except Exception as e:
        return await handle_analysis_failure(session_id, label_names, db, e)


@router.get("/sessions/{session_id}/analysis")
async def get_analysis(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get session analysis report."""
    session = await load_session(session_id, user, db)

    if not session.ended_at:
        raise HTTPException(
            status_code=400,
            detail="Session must be ended before viewing analysis",
        )

    analysis_data = await load_analysis_response(session_id, db)
    if analysis_data is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis_data


@router.get("/sessions/{session_id}/analysis_page", response_class=HTMLResponse)
async def get_analysis_page(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Render analysis HTML page."""
    analysis_data = await get_analysis(session_id, user, db)

    return templates.TemplateResponse(
        "analysis.html",
        {
            "request": request,
            "user": user,
            "session_id": session_id,
            **(
                {"analysis_result_url": f"/sessions/{session_id}/analysis"}
                if "accepted_report" in analysis_data
                else {}
            ),
            **analysis_data,
        },
    )


@router.get(
    "/sessions/{session_id}/analysis_modal", response_class=HTMLResponse
)
async def get_analysis_modal(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Render analysis modal content (HTMX partial)."""
    analysis_data = await get_analysis(session_id, user, db)

    return templates.TemplateResponse(
        "partials/analysis_modal.html",
        {
            "request": request,
            "user": user,
            "session_id": session_id,
            **(
                {"analysis_result_url": f"/sessions/{session_id}/analysis"}
                if "accepted_report" in analysis_data
                else {}
            ),
            **analysis_data,
        },
    )


@router.post(
    "/sessions/{session_id}/analysis/detail-opened",
    status_code=204,
)
async def log_analysis_detail_opened(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Log that the user opened the analysis detail view."""
    await load_session(session_id, user, db)

    db.add(
        UiEvent(
            user_id=user.id,
            session_id=session_id,
            event_type="analysis_detail_opened",
        )
    )
    await db.commit()

    return Response(status_code=204)


@router.get("/sessions/{session_id}/export.csv")
async def export_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Export session with analysis to CSV."""
    await load_session(session_id, user, db)

    exporter = CSVExporter()
    try:
        csv_content = await exporter.export_session(session_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=session_{session_id}_analysis.csv"
            )
        },
    )

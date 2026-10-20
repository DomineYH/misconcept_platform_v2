"""Session analysis routes."""

from contextlib import asynccontextmanager
from datetime import timezone
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.api.routes.admin_session_export import demo_seed_session
from src.api.routes.session_helpers import (
    load_session,
    mark_session_ended,
)
from src.api.schemas import AnalysisRequest
from src.config import config
from src.i18n import display_language, translate
from src.models import (
    Session,
    UiEvent,
    User,
)
from src.services.analysis_results import (
    load_analysis_response,
)
from src.services.analysis_runs import (
    cancel_analysis,
    get_run,
    request_analysis,
    run_response,
)
from src.services.export import CSVExporter

router = APIRouter(tags=["Sessions"])
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


def demo_scope(request):
    """The request's demo ctx and its bound registry; normal keeps defaults."""
    ctx = getattr(request.state, "demo_context", None)
    if ctx is None:
        return {}
    # The module-level lifecycle has no visitors; demo uses the runtime's one.
    return dict(ctx=ctx, registry=request.app.state.demo_request_db.lifecycle)


@asynccontextmanager
async def guarded_db(request, db):
    """The session for helpers that hold write_guard(ctx) themselves.

    In demo the request session guards its own DML, which would re-enter
    that held guard; the helpers get a plain session of the same factory.
    """
    ctx = getattr(request.state, "demo_context", None)
    if ctx is None:
        yield db
        return
    async with ctx.factory() as raw:
        yield raw


@router.post("/sessions/{session_id}/end")
@limiter.limit("10/minute")
async def end_session(
    request: Request,
    session_id: int,
    body: AnalysisRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Commit the end, then reserve analysis or return its confirmation plan."""
    session = await load_session(
        session_id, user, db, language=display_language(request)
    )
    async with guarded_db(request, db) as write_db:
        if write_db is not db:
            session = await write_db.get(Session, session.id)
        ended_at, _ = await mark_session_ended(
            session,
            write_db,
            force=True,
            language=display_language(request),
            **demo_scope(request),
        )
        request_id = (
            body.request_id
            if body
            else str(
                uuid5(
                    NAMESPACE_URL,
                    f"teacher-end:{session_id}:{user.id}:{ended_at.replace(tzinfo=timezone.utc).isoformat()}",
                )
            )
        )
        return await request_analysis(
            write_db,
            session_id,
            user.id,
            request_id,
            plan_hash=body.plan_hash if body else None,
            language=display_language(request),
            **demo_scope(request),
        )


@router.post("/sessions/{session_id}/analyze")
@limiter.limit("5/minute")
async def analyze_session_endpoint(
    request: Request,
    body: AnalysisRequest,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Reserve an analysis independently of the response connection."""
    await load_session(session_id, user, db, language=display_language(request))
    async with guarded_db(request, db) as write_db:
        return await request_analysis(
            write_db,
            session_id,
            user.id,
            body.request_id,
            plan_hash=body.plan_hash,
            language=display_language(request),
            **demo_scope(request),
        )


@router.get("/sessions/{session_id}/analysis")
async def get_analysis(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
) -> dict:
    """Get session analysis report."""
    session = await load_session(
        session_id, user, db, language=display_language(request)
    )

    if not session.ended_at:
        raise HTTPException(
            status_code=400,
            detail=translate(
                "teacher.end_before_analysis", display_language(request)
            ),
        )

    analysis_data = await load_analysis_response(session_id, db)
    if analysis_data is None:
        raise HTTPException(
            status_code=404,
            detail=translate(
                "teacher.analysis_not_found", display_language(request)
            ),
        )
    return analysis_data


@router.get("/sessions/{session_id}/analysis_page", response_class=HTMLResponse)
async def get_analysis_page(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Render analysis HTML page."""
    analysis_data = await get_analysis(session_id, user, db, request=request)

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
    analysis_data = await get_analysis(session_id, user, db, request=request)

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
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Log that the user opened the analysis detail view."""
    await load_session(session_id, user, db, language=display_language(request))

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
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Export session with analysis to CSV."""
    await load_session(session_id, user, db, language=display_language(request))

    exporter = CSVExporter()
    try:
        csv_content = await exporter.export_session(
            session_id, db, demo_seed_session_id=demo_seed_session(request)
        )
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=translate(
                "teacher.session_not_found", display_language(request)
            ),
        )

    return Response(
        content="\ufeff" + csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=demo_d0-02-v1_teacher_session-{session_id}.csv"
                if config.APP_MODE == "demo"
                else f"attachment; filename=session_{session_id}_analysis.csv"
            )
        },
    )


@router.get("/sessions/{session_id}/analysis/runs/{run_id}")
async def get_analysis_run(
    request: Request,
    session_id: int,
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await load_session(session_id, user, db, language=display_language(request))
    return await run_response(
        db,
        await get_run(
            db, session_id, run_id, language=display_language(request)
        ),
    )


@router.post(
    "/sessions/{session_id}/analysis/runs/{run_id}/cancel", status_code=202
)
async def cancel_analysis_run(
    request: Request,
    session_id: int,
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await load_session(session_id, user, db, language=display_language(request))
    async with guarded_db(request, db) as write_db:
        return await cancel_analysis(
            write_db,
            session_id,
            run_id,
            user.id,
            language=display_language(request),
            **demo_scope(request),
        )

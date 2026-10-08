"""Admin session action routes."""

import logging

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.session_helpers import mark_session_ended
from src.config import config
from src.models import (
    AnalysisFramework,
    Message,
)
from src.models.scenario import Scenario
from src.models.session import Session
from src.models.user import User
from src.services.analysis_pipeline import analyze_session, run_llm_pipeline
from src.services.analysis_results import (
    load_analysis_response as _load_analysis_response,
)
from src.services.analysis_results import save_analysis

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


@router.post(
    "/admin/sessions/{session_id}/end",
    response_class=HTMLResponse,
)
async def end_session(
    request: Request,
    session_id: int,
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

    if session.ended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session already ended",
        )

    await mark_session_ended(session, db)

    # Trigger analysis
    try:
        scenario_result = await db.execute(
            select(Scenario).where(Scenario.id == session.scenario_id)
        )
        scenario = scenario_result.scalar_one_or_none()
        if scenario and scenario.framework_id:
            framework_result = await db.execute(
                select(AnalysisFramework).where(
                    AnalysisFramework.id == scenario.framework_id
                )
            )
            framework = framework_result.scalar_one_or_none()
            if framework:
                await analyze_session(
                    session_id,
                    session,
                    scenario,
                    framework,
                    db,
                )
    except Exception as e:
        await db.rollback()
        logger.warning(f"Analysis failed for session {session_id}: {e}")

    # Rollback expires scalar and relationship attributes; reload all render inputs.
    session = (
        await db.execute(
            query.options(joinedload(Session.summary)).execution_options(
                populate_existing=True
            )
        )
    ).scalar_one()

    return templates.TemplateResponse(
        "partials/session_row.html",
        {"request": request, "session": session},
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
        {"request": request, "session": session},
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

    analysis_data = await _load_analysis_response(session_id, db)
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
            **analysis_data,
        },
    )


@router.post("/admin/sessions/{session_id}/analyze_regenerate")
@limiter.limit("2/minute")
async def regenerate_analysis(
    request: Request,
    session_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Regenerate session analysis (admin-only).

    Runs full synthesis pipeline. On LLM failure, old data is preserved.
    On success, replaces old report and summary atomically.
    """
    query = (
        select(Session)
        .options(joinedload(Session.scenario))
        .where(Session.id == session_id)
    )
    result = await db.execute(query)
    session = result.unique().scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if not session.ended_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session must be ended before analysis",
        )

    scenario = session.scenario
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )

    framework_result = await db.execute(
        select(AnalysisFramework).where(
            AnalysisFramework.id == scenario.framework_id
        )
    )
    framework = framework_result.scalar_one_or_none()
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Framework not found",
        )

    # Load all messages
    all_messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    all_messages = all_messages_result.scalars().all()
    teacher_messages = [m for m in all_messages if m.role == "teacher"]

    await db.commit()  # Release the read transaction before external calls.

    # Run LLM pipeline before replacing results; attempts use separate transactions.
    try:
        (
            distribution,
            question_analyses,
            payload,
            synthesis_status,
            synth_model,
            synth_hash,
            api_usage_logs,
        ) = await run_llm_pipeline(
            session_id,
            all_messages,
            teacher_messages,
            scenario,
            framework,
            async_sessionmaker(
                db.bind, expire_on_commit=False, autoflush=False
            ),
            session.teacher_id,
        )
    except Exception as e:
        logger.error(
            "Regeneration LLM failed for session %d: %s",
            session_id,
            e,
            exc_info=True,
        )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis regeneration failed",
        )

    saved = await save_analysis(
        session_id,
        (
            distribution,
            question_analyses,
            payload,
            synthesis_status,
            synth_model,
            synth_hash,
            api_usage_logs,
        ),
        db,
        regenerate=True,
    )

    analysis_data = await _load_analysis_response(session_id, db)
    if analysis_data is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis regeneration failed",
        )
    analysis_data["regeneration_status"] = saved["regeneration_status"]
    return analysis_data

"""Admin session action routes."""

import json
import logging
from datetime import datetime, timezone

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
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.config import config
from src.models import (
    AnalysisFramework,
    Message,
    QuestionAnalysis,
    SessionFeedbackReport,
    SessionSummary,
)
from src.models.scenario import Scenario
from src.models.session import Session
from src.models.user import User
from src.services.analysis_pipeline import analyze_session, run_llm_pipeline
from src.utils.analysis_helpers import parse_reasoning
from src.utils.session_feedback import (
    derive_plain_feedback,
    load_feedback_sections,
)

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


async def _load_existing_report_status(
    session_id: int,
    db: AsyncSession,
) -> str | None:
    """Return the persisted feedback_report status, or None if no row exists."""
    result = await db.execute(
        select(SessionFeedbackReport.status).where(
            SessionFeedbackReport.session_id == session_id
        )
    )
    return result.scalar_one_or_none()


async def _begin_regeneration_write_lock(db: AsyncSession) -> None:
    """Start the SQLite replacement window with an EXCLUSIVE lock.

    Regeneration performs the LLM work before touching the database, then
    deletes/re-inserts the one-to-one analysis rows. SQLite's default
    deferred transactions do not reserve the writer slot until the first
    write, so use BEGIN EXCLUSIVE for that short replacement window.
    """
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return

    if db.in_transaction():
        await db.rollback()

    await db.execute(text("BEGIN EXCLUSIVE"))


async def _load_analysis_response(
    session_id: int,
    db: AsyncSession,
) -> dict | None:
    """Load the current persisted analysis in response/modal shape."""
    summary_result = await db.execute(
        select(SessionSummary).where(SessionSummary.session_id == session_id)
    )
    summary = summary_result.scalar_one_or_none()
    if summary is None:
        return None

    report_result = await db.execute(
        select(SessionFeedbackReport).where(
            SessionFeedbackReport.session_id == session_id
        )
    )
    feedback_report = report_result.scalar_one_or_none()
    feedback_status = feedback_report.status if feedback_report else "legacy"
    feedback_sections = await load_feedback_sections(session_id, db)

    session_result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = session_result.scalar_one()

    stats_result = await db.execute(
        select(Message.role, func.count())
        .where(Message.session_id == session_id)
        .group_by(Message.role)
    )
    role_counts = dict(stats_result.all())

    duration_seconds = None
    if session.ended_at and session.started_at:
        duration_seconds = int(
            (session.ended_at - session.started_at).total_seconds()
        )

    teacher_rows_result = await db.execute(
        select(Message, QuestionAnalysis)
        .outerjoin(
            QuestionAnalysis,
            Message.id == QuestionAnalysis.message_id,
        )
        .where(Message.session_id == session_id)
        .where(Message.role == "teacher")
        .order_by(Message.created_at)
    )
    teacher_rows = teacher_rows_result.all()

    questions = []
    teacher_label_by_msg_id: dict[int, tuple[str | None, str | None]] = {}
    for msg, analysis in teacher_rows:
        reasoning = None
        if analysis and analysis.meta_json:
            reasoning = parse_reasoning(analysis.meta_json)
        label = analysis.label if analysis else None
        grade = analysis.grade if analysis else None
        teacher_label_by_msg_id[msg.id] = (label, grade)
        questions.append(
            {
                "content": msg.content,
                "label": label or "Unclassified",
                "grade": grade,
                "confidence": analysis.confidence if analysis else None,
                "reasoning": reasoning,
                "created_at": msg.created_at.isoformat(),
            }
        )

    # Compute grade counts for group summary
    grade_counts = {"우수": 0, "개선": 0}
    for q in questions:
        g = q.get("grade")
        if g in grade_counts:
            grade_counts[g] += 1

    # Issue #33: derive level from persisted grade (not framework lookup) so
    # historical sessions stay consistent across framework edits.
    framework_label_criteria: dict[str, str] = {}
    scenario = await db.get(Scenario, session.scenario_id)
    if scenario and scenario.framework_id:
        framework = await db.get(AnalysisFramework, scenario.framework_id)
        if framework:
            framework_label_criteria = dict(framework.label_criteria_map)

    grade_to_level = {"우수": "high", "개선": "low"}

    all_messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    messages_payload = []
    for m in all_messages_result.scalars().all():
        label = grade = level = None
        if m.role == "teacher" and m.id in teacher_label_by_msg_id:
            label, grade = teacher_label_by_msg_id[m.id]
            level = grade_to_level.get(grade)
        messages_payload.append(
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "label": label,
                "grade": grade,
                "level": level,
            }
        )

    return {
        "distribution": summary.distribution,
        "feedback": summary.feedback,
        "feedback_status": feedback_status,
        "feedback_sections": feedback_sections,
        "stats": {
            "duration_seconds": duration_seconds,
            "teacher_question_count": role_counts.get("teacher", 0),
            "student_response_count": role_counts.get("student", 0),
            "tutor_intervention_count": session.tutor_intervention_count,
        },
        "questions": questions,
        "messages": messages_payload,
        "framework_label_criteria": framework_label_criteria,
        "grade_counts": grade_counts,
        "session_ended_at": session.ended_at.isoformat(),
    }


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

    session.ended_at = datetime.now(timezone.utc)
    await db.flush()

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
        logger.warning(f"Analysis failed for session {session_id}: {e}")

    # Reload with summary
    await db.refresh(session, ["summary"])

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

    # Run LLM pipeline (no DB writes) — synthesize FIRST
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
            session_id, all_messages, teacher_messages, scenario, framework
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

    feedback = (
        derive_plain_feedback(payload)
        if synthesis_status != "failed"
        else (
            "분석에 실패했습니다. "
            "잠시 후 다시 시도하거나 관리자에게 문의하세요."
        )
    )

    if synthesis_status == "failed":
        preserved = await _load_analysis_response(session_id, db)
        if preserved is not None:
            logger.warning(
                "Regeneration synthesis failed for session %d; "
                "preserving existing analysis",
                session_id,
            )
            preserved["regeneration_status"] = "synthesis_failed_preserved"
            return preserved

    if synthesis_status == "degraded":
        existing_status = await _load_existing_report_status(session_id, db)
        if existing_status == "ok":
            preserved = await _load_analysis_response(session_id, db)
            if preserved is not None:
                logger.warning(
                    "Regeneration produced degraded payload for session %d "
                    "but existing report is ok; preserving existing analysis",
                    session_id,
                )
                preserved["regeneration_status"] = "degraded_skipped_preserved"
                return preserved

    await _begin_regeneration_write_lock(db)

    # Delete old data inside the locked replacement window.
    msg_ids_subquery = select(Message.id).where(
        Message.session_id == session_id
    )
    await db.execute(
        sql_delete(QuestionAnalysis).where(
            QuestionAnalysis.message_id.in_(msg_ids_subquery)
        )
    )
    await db.execute(
        sql_delete(SessionFeedbackReport).where(
            SessionFeedbackReport.session_id == session_id
        )
    )
    await db.execute(
        sql_delete(SessionSummary).where(
            SessionSummary.session_id == session_id
        )
    )

    # Insert new data
    for qa in question_analyses:
        db.add(qa)

    db.add(
        SessionFeedbackReport(
            session_id=session_id,
            version=1,
            model=synth_model,
            prompt_hash=synth_hash,
            status=synthesis_status,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )

    db.add(
        SessionSummary(
            session_id=session_id,
            distribution_json=json.dumps(distribution),
            feedback=feedback,
        )
    )
    for log_entry in api_usage_logs:
        db.add(log_entry)

    await db.commit()

    analysis_data = await _load_analysis_response(session_id, db)
    if analysis_data is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis regeneration failed",
        )
    analysis_data["regeneration_status"] = "replaced"
    return analysis_data

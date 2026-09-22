"""Session analysis routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.api.routes.session_helpers import load_session, mark_session_ended
from src.config import config
from src.models import (
    AnalysisFramework,
    Message,
    QuestionAnalysis,
    Scenario,
    SessionFeedbackReport,
    SessionSummary,
    UiEvent,
    User,
)
from src.services.analysis_pipeline import (
    analyze_session,
    handle_analysis_failure,
    handle_duplicate_session_state,
)
from src.services.export import CSVExporter
from src.utils.analysis_helpers import parse_reasoning
from src.utils.session_feedback import load_feedback_sections

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

    # Session must be ended before analysis
    if not session.ended_at:
        raise HTTPException(
            status_code=400,
            detail="Session must be ended before analysis",
        )

    # Check for existing summary
    existing_summary_result = await db.execute(
        select(SessionSummary).where(SessionSummary.session_id == session_id)
    )
    existing_summary = existing_summary_result.scalar_one_or_none()

    if existing_summary:
        return {
            "distribution": existing_summary.distribution,
            "feedback": existing_summary.feedback,
        }

    # Load scenario and framework
    scenario_result = await db.execute(
        select(Scenario).where(Scenario.id == session.scenario_id)
    )
    scenario = scenario_result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    framework_result = await db.execute(
        select(AnalysisFramework).where(
            AnalysisFramework.id == scenario.framework_id
        )
    )
    framework = framework_result.scalar_one_or_none()
    if not framework:
        raise HTTPException(status_code=404, detail="Framework not found")

    try:
        return await analyze_session(
            session_id, session, scenario, framework, db
        )
    except IntegrityError as e:
        return await handle_duplicate_session_state(
            session_id, framework, db, e
        )
    except Exception as e:
        return await handle_analysis_failure(session_id, framework, db, e)


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

    summary_result = await db.execute(
        select(SessionSummary).where(SessionSummary.session_id == session_id)
    )
    summary = summary_result.scalar_one_or_none()

    if not summary:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Compute feedback_status from SessionFeedbackReport
    report_result = await db.execute(
        select(SessionFeedbackReport).where(
            SessionFeedbackReport.session_id == session_id
        )
    )
    feedback_report = report_result.scalar_one_or_none()
    feedback_status = feedback_report.status if feedback_report else "legacy"

    # Load feedback_sections
    feedback_sections = await load_feedback_sections(session_id, db)

    # Compute stats via aggregate query
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

    stats = {
        "duration_seconds": duration_seconds,
        "teacher_question_count": role_counts.get("teacher", 0),
        "student_response_count": role_counts.get("student", 0),
        "tutor_intervention_count": session.tutor_intervention_count,
    }

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

    # Load framework only for the criteria map (used by 우수/개선 cards).
    # Issue #33: `level` is now derived from the persisted `grade` so that
    # historical sessions stay consistent if an admin later edits a label's
    # level. grade → level mapping is the single source of truth.
    framework_label_criteria: dict[str, str] = {}
    scenario = await db.get(Scenario, session.scenario_id)
    if scenario and scenario.framework_id:
        framework = await db.get(AnalysisFramework, scenario.framework_id)
        if framework:
            framework_label_criteria = dict(framework.label_criteria_map)

    grade_to_level = {"우수": "high", "개선": "low"}

    # Build all-message timeline with teacher level annotations.
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
        "stats": stats,
        "questions": questions,
        "messages": messages_payload,
        "framework_label_criteria": framework_label_criteria,
        "grade_counts": grade_counts,
        "session_ended_at": session.ended_at.isoformat(),
    }


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

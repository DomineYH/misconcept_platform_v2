"""Persist one analysis per session; serialize replacement after LLM work."""

import json
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    QuestionAnalysis,
    Session,
    SessionFeedbackReport,
    SessionSummary,
    User,
)
from src.models.provider_connection import now
from src.services.analysis_projection import public_analysis
from src.services.lesson_snapshots import read_lesson_snapshot
from src.services.session_history import session_display
from src.utils.analysis_helpers import parse_reasoning
from src.utils.session_feedback import (
    FALLBACK_FEEDBACK,
    derive_plain_feedback,
    load_feedback_sections,
)


def analysis_display(session):
    """Native IDs use frozen names; historical strings retain their meaning."""
    if session.snapshot_origin != "native":
        return None, {}
    analysis = read_lesson_snapshot(session).config.analysis
    if not analysis.classification_enabled:
        return False, {}
    return analysis.classification_enabled, {
        r.id: r.name for r in analysis.rubric
    }


def analysis_status(summary, report):
    if report is not None:
        if (
            report.version == 2
            and json.loads(report.payload_json)
            .get("metadata", {})
            .get("outcome")
            == "no_dialogue"
        ):
            return "no_dialogue"
        return report.status
    # Compatibility only: old fallback rows predate explicit report status.
    return "failed" if summary.feedback == FALLBACK_FEEDBACK else "legacy"


def summary_response(summary, report):
    status = analysis_status(summary, report)
    result = {
        "distribution": summary.distribution,
        "feedback": summary.feedback,
        "feedback_status": status,
        "retryable": status in {"failed", "degraded"},
    }
    if status == "failed":
        result["error"] = "analysis_failed"
    return result


async def load_summary(session_id, db):
    summary = (
        await db.scalars(
            select(SessionSummary)
            .where(SessionSummary.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    report = (
        await db.scalars(
            select(SessionFeedbackReport)
            .where(SessionFeedbackReport.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    return summary, report


async def save_analysis(
    session_id, result, db, *, regenerate=False, run_id=None
):
    distribution, questions, payload, status, model, prompt_hash, usage = result
    feedback = (
        (
            payload["brief_feedback"][0]
            if payload.get("schema_version") == 2
            else derive_plain_feedback(payload)
        )
        if status != "failed"
        else FALLBACK_FEEDBACK
    )
    await db.commit()
    try:
        # SQLite writer reservation covers state check + complete replacement.
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id) if run_id else None
        if run_id:
            from src.services.analysis_pipeline import load_analysis_lesson
            from src.services.analysis_runs import RUN_SECONDS

            payload.setdefault("metadata", {}).update(
                run_id=run_id, request_id=run.request_id if run else None
            )
            if run is None or run.status != "running":
                await db.rollback()
                return None
            if (
                now() - run.started_at.replace(tzinfo=timezone.utc)
            ).total_seconds() >= RUN_SECONDS:
                status = "failed"
                payload["metadata"]["error_code"] = "timeout_total"
            else:
                try:
                    await load_analysis_lesson(db, session_id, run.owner_id)
                    if json.loads(run.plan_json).get("regenerate"):
                        actor = await db.get(User, run.owner_id)
                        if not actor.is_admin:
                            raise HTTPException(403, detail="Forbidden")
                except HTTPException:
                    status = "failed"
                    payload["metadata"][
                        "error_code"
                    ] = "configuration_unavailable"
            run.status = status
            run.finished_at = now()
            run.error_code = payload.get("metadata", {}).get("error_code")
            run.outcome_json = json.dumps(
                dict(
                    status=status,
                    error_code=run.error_code,
                    coverage=payload.get("metadata", {}).get("coverage"),
                    outcome=(
                        payload.get("metadata", {}).get("outcome", status)
                        if status != "failed"
                        else "failed"
                    ),
                )
            )
            if status == "failed":
                await db.commit()
                return None
        summary, report = await load_summary(session_id, db)
        if summary:
            current = analysis_status(summary, report)
            preserve = (
                (not regenerate and current not in {"failed", "degraded"})
                or status == "failed"
                or (status == "degraded" and current in {"ok", "legacy"})
            )
            if preserve:
                response = summary_response(summary, report)
                if regenerate:
                    response["regeneration_status"] = (
                        "synthesis_failed_preserved"
                        if status == "failed"
                        else "degraded_skipped_preserved"
                    )
                await db.commit()
                return response
        message_ids = select(Message.id).where(Message.session_id == session_id)
        await db.execute(
            delete(QuestionAnalysis).where(
                QuestionAnalysis.message_id.in_(message_ids)
            )
        )
        await db.execute(
            delete(SessionFeedbackReport).where(
                SessionFeedbackReport.session_id == session_id
            )
        )
        await db.execute(
            delete(SessionSummary).where(
                SessionSummary.session_id == session_id
            )
        )
        report = SessionFeedbackReport(
            session_id=session_id,
            version=payload.get("schema_version", 1),
            model=model,
            prompt_hash=prompt_hash,
            status=status,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        summary = SessionSummary(
            session_id=session_id,
            distribution_json=json.dumps(distribution),
            feedback=feedback,
        )
        db.add_all([*questions, report, summary, *usage])
        if run is not None:
            await db.flush()
            run.accepted_report_id = report.id
            run.accepted_report_version = report.version
        await db.commit()
        response = summary_response(summary, report)
        if regenerate:
            response["regeneration_status"] = "replaced"
        return response
    except BaseException:
        await db.rollback()
        raise


async def load_analysis_response(
    session_id: int,
    db: AsyncSession,
    *,
    admin=False,
) -> dict | None:
    """Load the current persisted analysis in response/modal shape."""
    connection = await db.connection()
    if not await connection.run_sync(
        lambda sync: sync.connection.driver_connection.in_transaction
    ):
        # SQLite legacy transaction control does not begin on SELECT.
        await db.execute(text("BEGIN"))
    summary_result = await db.execute(
        select(SessionSummary)
        .where(SessionSummary.session_id == session_id)
        .execution_options(populate_existing=True)
    )
    summary = summary_result.scalar_one_or_none()
    latest_run = await db.scalar(
        select(GenerationRun)
        .execution_options(populate_existing=True)
        .where(
            GenerationRun.session_id == session_id,
            GenerationRun.operation == "analysis",
        )
        .order_by(GenerationRun.started_at.desc(), GenerationRun.id.desc())
        .limit(1)
    )
    session = await db.get(Session, session_id)
    if (
        summary is None
        and latest_run is None
        and (
            session is None
            or session.snapshot_origin != "native"
            or not session.ended_at
        )
    ):
        return None

    report_result = await db.execute(
        select(SessionFeedbackReport)
        .where(SessionFeedbackReport.session_id == session_id)
        .execution_options(populate_existing=True)
    )
    feedback_report = report_result.scalar_one_or_none()
    feedback_status = (
        analysis_status(summary, feedback_report)
        if summary
        else latest_run.status if latest_run else "plan_required"
    )
    feedback_sections = await load_feedback_sections(session_id, db)

    session_result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = session_result.scalar_one()

    classification_enabled, label_names = analysis_display(session)

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
        .order_by(Message.created_at, Message.id)
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
                "message_id": msg.id,
                "content": msg.content,
                "label": label or "Unclassified",
                "label_name": label_names.get(label, label) or "Unclassified",
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
    grade_to_level = {"우수": "high", "개선": "low"}

    all_messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at, Message.id)
    )
    messages_payload = []
    for m in all_messages_result.scalars().all():
        label = grade = level = None
        if m.role == "teacher" and m.id in teacher_label_by_msg_id:
            label, grade = teacher_label_by_msg_id[m.id]
            level = grade_to_level.get(grade)
        messages_payload.append(
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "turn_index": m.turn_index,
                "label": label,
                "grade": grade,
                "level": level,
            }
        )

    response = {
        "distribution": summary.distribution if summary else {},
        "label_names": label_names,
        "classification_enabled": classification_enabled,
        "feedback": summary.feedback if summary else None,
        "feedback_status": feedback_status,
        "retryable": feedback_status in {"failed", "degraded"}
        and session.snapshot_origin == "native",
        **session_display(session),
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
        "session_ended_at": (
            session.ended_at.isoformat() if session.ended_at else None
        ),
    }
    if (
        summary is not None
        or latest_run is not None
        or session.snapshot_origin == "native"
    ):
        latest = await db.scalar(
            select(ApiUsageLog)
            .where(
                ApiUsageLog.session_id == session_id,
                ApiUsageLog.operation == "analysis_unified",
            )
            .order_by(ApiUsageLog.id.desc())
            .limit(1)
        )
        plan = None
        if (
            session.snapshot_origin == "native"
            and session.ended_at
            and (latest_run is None or latest_run.status != "running")
            and (
                summary is None
                or feedback_status in {"failed", "degraded"}
                or admin
            )
        ):
            from src.services.analysis_plan import load_plan, public_plan

            plan = public_plan(await load_plan(db, session, regenerate=admin))
        response.update(
            public_analysis(
                session,
                summary,
                feedback_report,
                messages_payload,
                label_names,
                classification_enabled,
                latest,
                admin=admin,
                run=latest_run,
                plan=plan,
            )
        )
    if latest_run is not None and json.loads(latest_run.plan_json or "{}").get(
        "regenerate"
    ):
        response["regeneration_status"] = (
            "replaced"
            if latest_run.accepted_report_id is not None
            else (
                "synthesis_failed_preserved"
                if latest_run.status == "failed" and summary is not None
                else (
                    "degraded_skipped_preserved"
                    if latest_run.status == "degraded" and summary is not None
                    else latest_run.status
                )
            )
        )
    return response

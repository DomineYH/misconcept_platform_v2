"""Persist one analysis per session; serialize replacement after LLM work."""
import json

from sqlalchemy import delete, select, text

from src.models import Message, QuestionAnalysis, SessionFeedbackReport, SessionSummary
from src.utils.session_feedback import FALLBACK_FEEDBACK, derive_plain_feedback


def analysis_status(summary, report):
    if report is not None:
        return report.status
    # Compatibility only: old fallback rows predate explicit report status.
    return "failed" if summary.feedback == FALLBACK_FEEDBACK else "legacy"


def summary_response(summary, report):
    status = analysis_status(summary, report)
    result = {"distribution": summary.distribution, "feedback": summary.feedback,
              "feedback_status": status, "retryable": status == "failed"}
    if status == "failed":
        result["error"] = "analysis_failed"
    return result


async def load_summary(session_id, db):
    summary = (await db.scalars(select(SessionSummary).where(
        SessionSummary.session_id == session_id
    ).execution_options(populate_existing=True))).one_or_none()
    report = (await db.scalars(select(SessionFeedbackReport).where(
        SessionFeedbackReport.session_id == session_id
    ).execution_options(populate_existing=True))).one_or_none()
    return summary, report


async def save_analysis(session_id, result, db, *, regenerate=False):
    distribution, questions, payload, status, model, prompt_hash, usage = result
    feedback = derive_plain_feedback(payload) if status != "failed" else FALLBACK_FEEDBACK
    await db.commit()
    try:
        # SQLite writer reservation covers state check + complete replacement.
        await db.execute(text("BEGIN IMMEDIATE"))
        summary, report = await load_summary(session_id, db)
        if summary:
            current = analysis_status(summary, report)
            preserve = (
                (not regenerate and current != "failed")
                or (regenerate and status == "failed")
                or (regenerate and status == "degraded" and current in {"ok", "legacy"})
            )
            if preserve:
                response = summary_response(summary, report)
                if regenerate:
                    response["regeneration_status"] = (
                        "synthesis_failed_preserved" if status == "failed"
                        else "degraded_skipped_preserved"
                    )
                await db.commit()
                return response
        message_ids = select(Message.id).where(Message.session_id == session_id)
        await db.execute(delete(QuestionAnalysis).where(QuestionAnalysis.message_id.in_(message_ids)))
        await db.execute(delete(SessionFeedbackReport).where(SessionFeedbackReport.session_id == session_id))
        await db.execute(delete(SessionSummary).where(SessionSummary.session_id == session_id))
        report = SessionFeedbackReport(session_id=session_id, version=1, model=model,
                                       prompt_hash=prompt_hash, status=status,
                                       payload_json=json.dumps(payload, ensure_ascii=False))
        summary = SessionSummary(session_id=session_id,
                                 distribution_json=json.dumps(distribution), feedback=feedback)
        db.add_all([*questions, report, summary, *usage])
        await db.commit()
        response = summary_response(summary, report)
        if regenerate:
            response["regeneration_status"] = "replaced"
        return response
    except BaseException:
        await db.rollback()
        raise

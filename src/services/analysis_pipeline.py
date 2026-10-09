"""Session analysis pipeline service.

Handles the analysis of teacher messages in a session, including
greeting detection, question classification, synthesis, and summary
generation.

All LLM calls complete before result writes; attempts use separate transactions.
On synthesis failure, rows are still persisted with status='failed'.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routes.session_helpers import (
    require_native_session,
    validate_scenario_access,
)
from src.models import (
    ApiUsageLog,
    AppSetting,
    Message,
    QuestionAnalysis,
    Session,
    User,
)
from src.services.analysis_results import (
    load_summary,
    save_analysis,
    summary_response,
)
from src.services.analyzer import Analyzer
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.lesson_snapshots import (
    configuration_error,
    read_lesson_snapshot,
)
from src.services.model_configuration import settings_values
from src.services.session_synthesizer import FAILED_PAYLOAD, SessionSynthesizer

logger = logging.getLogger(__name__)

FALLBACK_FEEDBACK = (
    "분석에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
)


async def load_analysis_lesson(db, session_id, actor_id):
    session = await db.get(Session, session_id)
    user = await db.get(User, actor_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, detail="Session not found")
    if user is None or (not user.is_admin and session.teacher_id != user.id):
        raise HTTPException(403, detail="Forbidden")
    require_native_session(session)
    if not session.ended_at:
        raise HTTPException(400, detail="Session must be ended before analysis")
    scenario = await validate_scenario_access(session.scenario_id, user, db)
    if not scenario.is_active:
        raise configuration_error()
    snapshot = read_lesson_snapshot(session)
    try:
        await resolve_frozen_model(
            db, snapshot.config.analysis.resolved_model_config, "analysis"
        )
    except InvocationError:
        raise configuration_error() from None
    return snapshot


async def analyze_session(
    session_id, session, db, *, actor_id=None, regenerate=False
):
    snapshot = await load_analysis_lesson(
        db, session_id, actor_id or session.teacher_id
    )
    all_messages_result = await db.execute(
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.role.in_(["teacher", "student"]),
        )
        .order_by(Message.created_at, Message.id)
    )
    all_messages = all_messages_result.scalars().all()
    teacher_messages = [m for m in all_messages if m.role == "teacher"]

    await db.commit()  # Inputs stay loaded (expire_on_commit=False); no lock across LLM.

    result = await run_llm_pipeline(
        session_id,
        all_messages,
        teacher_messages,
        snapshot,
        async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False),
        session.teacher_id,
        actor_id or session.teacher_id,
    )
    return await save_analysis(session_id, result, db, regenerate=regenerate)


async def run_llm_pipeline(
    session_id: int,
    all_messages: list[Message],
    teacher_messages: list[Message],
    snapshot,
    factory,
    owner_id=None,
    actor_id=None,
) -> tuple[
    dict,
    list[QuestionAnalysis],
    dict,
    str,
    str,
    str,
    list[ApiUsageLog],
]:
    """Run greeting, classification and synthesis before writing results.

    The common boundary independently commits and finalizes attempt rows.

    Returns:
        Tuple of (distribution, question_analyses, payload,
        synthesis_status, model, prompt_hash, empty legacy usage list).
    """
    framework = snapshot.config.analysis
    scenario = snapshot.scenario_context
    student = snapshot.config.student
    selection = framework.resolved_model_config
    analyzer = Analyzer(
        factory,
        selection=selection,
        session_id=session_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )

    # Step 1: Filter greeting messages
    if not framework.classification_enabled:
        teacher_messages = []
    if teacher_messages:
        teacher_messages = await _filter_greetings(
            session_id, teacher_messages, analyzer
        )

    # Step 2: Parallel classification with bounded semaphore
    distribution = (
        {r.id: 0 for r in framework.rubric}
        if framework.classification_enabled
        else {}
    )
    parallelism = 1
    async with factory() as settings_db:
        setting = await settings_db.get(AppSetting, 1)
        if setting is not None:
            limits, _ = settings_values(setting)
            parallelism = min(5, limits["total"], limits[selection.provider])
    semaphore = asyncio.Semaphore(parallelism)

    async def _classify_with_semaphore(msg: Message) -> dict:
        async with semaphore:
            context = "\n".join(f"{m.role}: {m.content}" for m in all_messages)
            return await analyzer.classify_question(
                question=msg.content,
                framework=framework,
                context=context,
                scenario_title=scenario.title,
                misconception_prompt=student.misconception,
                student_profile=student.internal_profile,
            )

    classification_results = await asyncio.gather(
        *[_classify_with_semaphore(msg) for msg in teacher_messages],
        return_exceptions=True,
    )

    question_analyses: list[QuestionAnalysis] = []
    for msg, result in zip(teacher_messages, classification_results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            logger.warning(f"Failed to analyze message {msg.id}: {result}")
            continue
        reasoning = result.get("reasoning")
        reasoning_json = (
            json.dumps(reasoning, ensure_ascii=False)
            if isinstance(reasoning, dict)
            else reasoning
        )
        question_analyses.append(
            QuestionAnalysis(
                message_id=msg.id,
                label=result["label"],
                confidence=result.get("confidence"),
                meta_json=reasoning_json,
                grade={"high": "우수", "low": "개선"}.get(
                    next(
                        r.level
                        for r in framework.rubric
                        if r.id == result["label"]
                    )
                ),
            )
        )
        distribution[result["label"]] += 1

    # Step 3: Synthesize session feedback
    messages_for_synthesis = [
        {"id": m.id, "role": m.role, "content": m.content} for m in all_messages
    ]
    qa_for_synthesis = [
        {
            "message_id": qa.message_id,
            "label": qa.label,
            "confidence": qa.confidence,
            "reasoning": qa.meta_json,
        }
        for qa in question_analyses
    ]

    try:
        synthesizer = SessionSynthesizer(
            factory,
            selection=selection,
            actor_id=actor_id,
            session_id=session_id,
            owner_id=owner_id,
            request_id=analyzer.request_id,
        )
        payload, synthesis_status = await synthesizer.synthesize(
            messages=messages_for_synthesis,
            question_analyses=qa_for_synthesis,
            scenario=scenario.title,
            misconception=student.misconception,
            student_profile=student.internal_profile,
            framework=framework,
        )
        synth_model = synthesizer.model
        synth_hash = synthesizer._hash
    except Exception as e:
        logger.error(
            "Session %d: synthesis failed: %s",
            session_id,
            e,
            exc_info=True,
        )
        payload = dict(FAILED_PAYLOAD)
        synthesis_status = "failed"
        synth_model = "unknown"
        synth_hash = "unknown"

    if (
        analyzer.greeting_failed
        or any(isinstance(r, Exception) for r in classification_results)
    ) and synthesis_status != "failed":
        synthesis_status = "degraded"

    return (
        distribution,
        question_analyses,
        payload,
        synthesis_status,
        synth_model,
        synth_hash,
        [],
    )


async def _filter_greetings(
    session_id: int,
    teacher_messages: list[Message],
    analyzer: Analyzer,
) -> list[Message]:
    """Filter greeting messages from analysis."""
    greeting_results = await analyzer.detect_greetings(
        [m.content for m in teacher_messages]
    )

    filtered_messages = []
    for msg, result in zip(teacher_messages, greeting_results):
        if not result.get("is_greeting", False):
            filtered_messages.append(msg)
        else:
            logger.info(
                f"Filtered greeting message {msg.id}: "
                f"{result.get('reason', 'greeting')}"
            )

    filtered_count = len(teacher_messages) - len(filtered_messages)
    if filtered_count > 0:
        logger.info(
            f"Session {session_id}: Filtered {filtered_count} "
            f"greeting messages from analysis"
        )

    return filtered_messages


async def handle_duplicate_session_state(
    session_id: int,
    label_names: list[str],
    db: AsyncSession,
    error: IntegrityError,
) -> dict[str, Any]:
    """Handle duplicate insert from concurrent requests.

    Re-queries BOTH SessionSummary AND SessionFeedbackReport
    on IntegrityError. Returns unified response shape.
    """
    await db.rollback()
    logger.warning(
        f"Session {session_id}: duplicate session state detected: {error}"
    )
    summary, report = await load_summary(session_id, db)
    if summary:
        return summary_response(summary, report)
    return await create_fallback_summary(session_id, label_names, db)


# Backward-compatible alias for existing imports
handle_duplicate_summary = handle_duplicate_session_state


async def handle_analysis_failure(
    session_id: int,
    label_names: list[str],
    db: AsyncSession,
    error: Exception,
) -> dict[str, Any]:
    """Handle analysis pipeline failure with fallback."""
    await db.rollback()
    logger.error(
        f"Session {session_id}: analysis pipeline failed: {error}",
        exc_info=True,
    )

    return await create_fallback_summary(session_id, label_names, db)


async def create_fallback_summary(
    session_id: int,
    label_names: list[str],
    db: AsyncSession,
) -> dict[str, Any]:
    """Create fallback summary when analysis fails."""
    return await save_analysis(
        session_id,
        (
            {label: 0 for label in label_names},
            [],
            dict(FAILED_PAYLOAD),
            "failed",
            "unknown",
            "unknown",
            [],
        ),
        db,
    )

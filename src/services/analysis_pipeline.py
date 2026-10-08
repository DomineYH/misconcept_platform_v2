"""Session analysis pipeline service.

Handles the analysis of teacher messages in a session, including
greeting detection, question classification, synthesis, and summary
generation.

LLM-first / DB-second: all LLM calls complete before any DB writes.
On synthesis failure, rows are still persisted with status='failed'.
"""

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AnalysisFramework,
    ApiUsageLog,
    Message,
    QuestionAnalysis,
    Scenario,
    Session,
    calculate_cost,
)
from src.services.analysis_results import (
    load_summary,
    save_analysis,
    summary_response,
)
from src.services.analyzer import Analyzer
from src.services.session_synthesizer import FAILED_PAYLOAD, SessionSynthesizer

logger = logging.getLogger(__name__)

FALLBACK_FEEDBACK = (
    "분석에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
)


async def analyze_session(
    session_id: int,
    session: Session,
    scenario: Scenario,
    framework: AnalysisFramework,
    db: AsyncSession,
) -> dict[str, Any]:
    """Perform session analysis and create summary.

    LLM-first / DB-second: all LLM calls (greeting, classify, synthesize)
    complete before any DB writes. On synthesis failure, rows are still
    persisted with status='failed'.

    Args:
        session_id: Session ID
        session: Session object
        scenario: Scenario object
        framework: Analysis framework
        db: Database session

    Returns:
        Dict with distribution and feedback
    """
    all_messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    all_messages = all_messages_result.scalars().all()
    teacher_messages = [m for m in all_messages if m.role == "teacher"]

    await db.commit()  # Inputs stay loaded (expire_on_commit=False); no lock across LLM.

    result = await run_llm_pipeline(
        session_id, all_messages, teacher_messages, scenario, framework
    )
    return await save_analysis(session_id, result, db)


async def run_llm_pipeline(
    session_id: int,
    all_messages: list[Message],
    teacher_messages: list[Message],
    scenario: Scenario,
    framework: AnalysisFramework,
    *,
    client=None,
) -> tuple[
    dict,
    list[QuestionAnalysis],
    dict,
    str,
    str,
    str,
    list[ApiUsageLog],
]:
    """Run all LLM calls (greeting, classify, synthesize) without DB writes.

    Returns:
        Tuple of (distribution, question_analyses, payload,
        synthesis_status, model, prompt_hash, api_usage_logs).
    """
    async with AsyncExitStack() as stack:
        analyzer = await stack.enter_async_context(Analyzer(client=client))
        api_usage_logs: list[ApiUsageLog] = []

        # Step 1: Filter greeting messages
        if teacher_messages:
            teacher_messages = await _filter_greetings(
                session_id, teacher_messages, analyzer
            )
            log_entry = _build_api_usage_log(
                session_id=session_id,
                model=analyzer.model,
                usage_dict=analyzer.last_greeting_usage,
                operation="greeting",
            )
            if log_entry is not None:
                api_usage_logs.append(log_entry)

        # Step 2: Parallel classification with bounded semaphore
        distribution = {label: 0 for label in framework.label_names}
        msg_index_map = {msg.id: idx for idx, msg in enumerate(all_messages)}
        semaphore = asyncio.Semaphore(5)

        async def _classify_with_semaphore(msg: Message) -> dict:
            async with semaphore:
                msg_idx = msg_index_map[msg.id]
                context_messages = all_messages[max(0, msg_idx - 3) : msg_idx]
                context = "\n".join(
                    [f"{m.role}: {m.content}" for m in context_messages]
                )
                return await analyzer.classify_question(
                    question=msg.content,
                    framework=framework,
                    context=context,
                    scenario_title=scenario.title,
                    misconception_prompt=scenario.prompt,
                    student_profile=scenario.student_profile
                    or "Grade 5 student",
                )

        classification_results = await asyncio.gather(
            *[_classify_with_semaphore(msg) for msg in teacher_messages],
            return_exceptions=True,
        )

        question_analyses: list[QuestionAnalysis] = []
        for msg, result in zip(teacher_messages, classification_results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to analyze message {msg.id}: {result}")
                continue
            api_usage = result.pop("_api_usage", None)
            log_entry = _build_api_usage_log(
                session_id=session_id,
                model=analyzer.model,
                usage_dict=api_usage,
                operation="classification",
            )
            if log_entry is not None:
                api_usage_logs.append(log_entry)
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
                    grade=framework.labels_grade_map.get(result["label"]),
                )
            )
            distribution[result["label"]] += 1

        # Step 3: Synthesize session feedback
        messages_for_synthesis = [
            {"id": m.id, "role": m.role, "content": m.content}
            for m in all_messages
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
            synthesizer = await stack.enter_async_context(
                SessionSynthesizer(client=client)
            )
            payload, synthesis_status = await synthesizer.synthesize(
                messages=messages_for_synthesis,
                question_analyses=qa_for_synthesis,
                scenario=scenario.title,
                misconception=scenario.prompt,
                student_profile=scenario.student_profile or "Grade 5 student",
                framework=framework,
            )
            synth_model = synthesizer.model
            synth_hash = synthesizer._hash
            log_entry = _build_api_usage_log(
                session_id=session_id,
                model=synth_model,
                usage_dict=synthesizer.last_usage,
                operation="synthesis",
            )
            if log_entry is not None:
                api_usage_logs.append(log_entry)
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

        return (
            distribution,
            question_analyses,
            payload,
            synthesis_status,
            synth_model,
            synth_hash,
            api_usage_logs,
        )


def _build_api_usage_log(
    session_id: int,
    model: str,
    usage_dict: dict[str, int] | None,
    operation: str,
) -> ApiUsageLog | None:
    """Build an ApiUsageLog row for an analysis pipeline LLM operation."""
    if usage_dict is None:
        logger.debug(
            "No API usage info for session %d operation=%s model=%s",
            session_id,
            operation,
            model,
        )
        return None

    required = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(key not in usage_dict for key in required):
        logger.warning(
            "Incomplete API usage info for session %d operation=%s: %s",
            session_id,
            operation,
            usage_dict,
        )
        return None

    prompt_tokens = int(usage_dict["prompt_tokens"])
    completion_tokens = int(usage_dict["completion_tokens"])
    total_tokens = int(usage_dict["total_tokens"])
    return ApiUsageLog(
        session_id=session_id,
        bot_type="tutor",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=calculate_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        operation=operation,
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
    framework: AnalysisFramework | list[str],
    db: AsyncSession,
    error: IntegrityError,
) -> dict[str, Any]:
    """Handle duplicate insert from concurrent requests.

    Re-queries BOTH SessionSummary AND SessionFeedbackReport
    on IntegrityError. Returns unified response shape.
    """
    # Capture label names before rollback expires ORM attributes.
    label_names = (
        list(framework)
        if isinstance(framework, list)
        else list(framework.label_names)
    )
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
    framework: AnalysisFramework | list[str],
    db: AsyncSession,
    error: Exception,
) -> dict[str, Any]:
    """Handle analysis pipeline failure with fallback."""
    # Capture label names before rollback expires ORM attributes
    label_names = (
        list(framework)
        if isinstance(framework, list)
        else list(framework.label_names)
    )
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

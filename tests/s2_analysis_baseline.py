"""Pinned S2 baseline (66e2794) for isolated synthetic quality comparisons only."""

import asyncio
import json
import logging

from src.models import ApiUsageLog, AppSetting, Message, QuestionAnalysis
from src.services.analyzer import Analyzer
from src.services.model_configuration import settings_values
from src.services.session_synthesizer import FAILED_PAYLOAD, SessionSynthesizer

logger = logging.getLogger(__name__)


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
    analysis = snapshot.config.analysis
    scenario = snapshot.scenario_context
    student = snapshot.config.student
    selection = analysis.resolved_model_config
    analyzer = Analyzer(
        factory,
        selection=selection,
        session_id=session_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )

    # Step 1: Filter greeting messages
    if not analysis.classification_enabled:
        teacher_messages = []
    if teacher_messages:
        teacher_messages = await _filter_greetings(
            session_id, teacher_messages, analyzer
        )

    # Step 2: Parallel classification with bounded semaphore
    distribution = (
        {r.id: 0 for r in analysis.rubric}
        if analysis.classification_enabled
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
                analysis=analysis,
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
                        for r in analysis.rubric
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
            analysis=analysis,
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

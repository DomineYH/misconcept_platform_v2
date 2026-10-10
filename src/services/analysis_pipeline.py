"""Session analysis pipeline service.

Reviews the full frozen dialogue in one structured provider call.

All LLM calls complete before result writes; attempts use separate transactions.
Failed attempts preserve an existing accepted report.
"""

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routes.session_helpers import (
    require_native_session,
    validate_scenario_access,
)
from src.models import (
    ApiUsageLog,
    Message,
    QuestionAnalysis,
    Session,
    User,
)
from src.models.provider_connection import now
from src.services.analysis_invocations import AnalysisCaller
from src.services.analysis_output_contract import UnifiedAnalysisOutput
from src.services.analysis_results import save_analysis
from src.services.analysis_statistics import analysis_statistics
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.lesson_snapshots import (
    canonical_hash,
    configuration_error,
    read_lesson_snapshot,
)
from src.services.session_synthesizer import FAILED_PAYLOAD, prompt_hash
from src.utils.cache import load_prompt_template

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
    *,
    request_id=None,
    run_id=None,
) -> tuple[
    dict,
    list[QuestionAnalysis],
    dict,
    str,
    str,
    str,
    list[ApiUsageLog],
]:
    """One strict call reviews the full stored teacher/student transcript."""
    analysis = snapshot.config.analysis
    selection = analysis.resolved_model_config
    caller = AnalysisCaller(
        factory,
        selection=selection,
        session_id=session_id,
        owner_id=owner_id,
        actor_id=actor_id,
        request_id=request_id,
        run_id=run_id,
    )
    messages = [
        dict(id=m.id, role=m.role, content=m.content) for m in all_messages
    ]
    template = load_prompt_template("analysis_v2.txt")
    source_hash = prompt_hash(template)
    context = dict(
        messages=messages,
        labels={r.id: r.level for r in analysis.rubric},
        classification_enabled=analysis.classification_enabled,
    )
    inputs = dict(
        messages=messages,
        scenario=snapshot.scenario_context.model_dump(),
        problem=snapshot.config.problem.model_dump(),
        student=snapshot.config.student.model_dump(
            exclude={"resolved_model_config"}
        ),
        analysis=analysis.model_dump(exclude={"resolved_model_config"}),
    )
    payload = dict(
        schema_version=2,
        message_classifications=[],
        misconception_findings=[],
        brief_feedback=["대화가 없어 분석할 수 없습니다."],
        strengths=[],
        improvements=[],
        dialogue_coaching=[],
    )
    status, error_code = "ok", None
    if messages:
        try:
            payload, _ = await caller.structured(
                template
                + "\n입력 JSON\n"
                + json.dumps(inputs, ensure_ascii=False),
                UnifiedAnalysisOutput,
                "analysis_unified",
                validation_context=context,
            )
        except InvocationError as error:
            error_code = error.code
            payload["brief_feedback"] = [FALLBACK_FEEDBACK]
            status = "failed"
    distribution, questions, coverage, status, error_code = analysis_statistics(
        all_messages,
        payload,
        analysis,
        context,
        status,
        error_code,
    )
    payload["metadata"] = dict(
        coverage=coverage,
        mode="single",
        estimator_version=None,
        request_id=caller.request_id,
        run_id=run_id,
        config_hash=canonical_hash(snapshot.model_dump()),
        prompt_version=source_hash,
        schema_version=2,
        generated_at=now().isoformat(),
        error_code=error_code,
        outcome="no_dialogue" if not messages else status,
    )
    return (
        distribution,
        questions,
        payload,
        status,
        selection.model_id,
        source_hash,
        [],
    )


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

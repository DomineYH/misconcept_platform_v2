"""Session analysis pipeline service.

Reviews the full frozen dialogue in one structured provider call.

All LLM calls complete before result writes; attempts use separate transactions.
Failed attempts preserve an existing accepted report.
"""

import hashlib
import json

from fastapi import HTTPException

from src.api.routes.session_helpers import (
    require_native_session,
    validate_scenario_access,
)
from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    QuestionAnalysis,
    Session,
    User,
)
from src.models.provider_connection import now
from src.services.analysis_invocations import AnalysisCaller
from src.services.analysis_output_contract import UnifiedAnalysisOutput
from src.services.analysis_plan import analysis_prompt, build_plan
from src.services.analysis_statistics import analysis_statistics
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.lesson_snapshots import (
    canonical_hash,
    configuration_error,
    read_lesson_snapshot,
)
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
    source_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    context = dict(
        messages=messages,
        labels={r.id: r.level for r in analysis.rubric},
        classification_enabled=analysis.classification_enabled,
    )
    if run_id:
        async with factory() as db:
            run = await db.get(GenerationRun, run_id)
            plan = json.loads(run.plan_json)
    else:
        plan = build_plan(snapshot, messages)
    budget_metadata = dict(
        estimator=plan["estimator_version"],
        formula=plan["formula"],
        estimated_input_tokens=plan["estimated_input_tokens"],
        estimated_output_tokens=plan["estimated_output_tokens"],
        input_budget_tokens=plan["input_budget_tokens"],
        reserved_output_tokens=plan.get("frozen_output_cap"),
        reserved_thinking_tokens=plan["reserved_thinking_tokens"],
        capability_definition_version=(
            plan["limits"]["definition_version"] if plan["limits"] else None
        ),
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
            if plan["mode"] != "single":
                raise InvocationError("context_limit")
            payload, _ = await caller.structured(
                analysis_prompt(snapshot, messages),
                UnifiedAnalysisOutput,
                "analysis_unified",
                validation_context=context,
                context_budget=budget_metadata,
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
        estimator_version=plan["estimator_version"],
        formula=plan["formula"],
        plan_hash=plan["plan_hash"],
        estimates=budget_metadata,
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

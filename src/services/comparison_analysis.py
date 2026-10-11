"""Single-call S4 requests and verified results without classroom persistence."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

from src.api.schemas.scenario_config import AnalysisConfig
from src.services.analysis_output_contract import UnifiedAnalysisOutput
from src.services.analysis_plan import (
    ESTIMATOR_VERSION,
    FORMULA,
    analysis_prompt,
    estimate_request,
    thinking_reserve,
)
from src.services.analysis_statistics import analysis_statistics
from src.services.comparison_state import utcnow
from src.services.context_budget import input_budget
from src.services.invocation_types import InvocationError, StructuredRequest
from src.services.lesson_snapshots import ScenarioContext
from src.services.model_capabilities import capabilities
from src.utils.cache import load_prompt_template

PROMPT_VERSION = hashlib.sha256(
    load_prompt_template("analysis_v2.txt").encode("utf-8")
).hexdigest()


def analysis_comparison_request(
    source, history, current_message, panel, options, request_id
):
    messages = history + (
        [dict(id=len(history) + 1, role="teacher", content=current_message)]
        if current_message
        else []
    )
    snapshot = SimpleNamespace(
        config=source.config,
        scenario_context=ScenarioContext(
            title=source.title.strip() or "제목 없는 초안",
            subject=source.subject,
            target_grade=source.target_grade,
        ),
    )
    request = StructuredRequest(
        panel.selection.provider,
        panel.selection.model_id,
        "analysis",
        "",
        [dict(role="user", content=analysis_prompt(snapshot, messages))],
        options,
        request_id,
        UnifiedAnalysisOutput,
        dict(
            messages=messages,
            labels={r.id: r.level for r in source.config.analysis.rubric},
            classification_enabled=source.config.analysis.classification_enabled,
        ),
    )
    cap = options.get(
        "max_output_tokens", 1024 if request.provider == "anthropic" else None
    )
    if cap is None:
        raise InvocationError("output_limit_missing")
    budget, _, version = input_budget(request)
    thinking = thinking_reserve(
        request.provider,
        options,
        capabilities(request.provider, request.model_id),
        cap,
    )
    estimated_input = estimate_request(request)
    estimated_output = (
        1200
        + sum(160 if m["role"] == "teacher" else 120 for m in messages)
        + thinking
    )
    if estimated_input > budget or estimated_output > cap:
        raise InvocationError("context_limit")
    return replace(
        request,
        context_budget_json=dict(
            estimator=ESTIMATOR_VERSION,
            formula=FORMULA,
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            input_budget_tokens=budget,
            reserved_output_tokens=cap,
            reserved_thinking_tokens=thinking,
            capability_definition_version=version,
        ),
    )


def analysis_comparison_result(accepted, request, event):
    context = request.validation_context
    payload = event.structured
    if event.type != "completed":
        if event.error_code not in {
            "invalid_output",
            "invalid_reference",
        } or not context.get("analysis_errors"):
            return None
        payload = context.get("validated_analysis")
    if payload is None:
        return None
    analysis = accepted["source_snapshot"]["config"]["analysis"]
    config = AnalysisConfig.model_validate(analysis)
    messages = context["messages"]
    distribution, _, coverage, status, error_code = analysis_statistics(
        [SimpleNamespace(**m, turn_id=None) for m in messages],
        payload,
        config,
        context,
        "ok",
        None,
    )
    total = sum(distribution.values())
    names = {r.id: r.name for r in config.rubric}
    report = dict(
        **payload,
        status=status,
        run_id=accepted["run_id"],
        created_at=utcnow().isoformat(),
        classification_enabled=config.classification_enabled,
        coverage=coverage,
        distribution=[
            dict(
                name=names[label],
                count=count,
                percentage=100 * count / total if total else None,
            )
            for label, count in distribution.items()
        ],
    )
    return status, dict(report=report, messages=messages), error_code

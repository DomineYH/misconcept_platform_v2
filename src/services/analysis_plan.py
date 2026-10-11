"""Versioned, conservative analysis plans; estimates never represent usage."""

import hashlib
import json
import math
from types import SimpleNamespace

from src.services.analysis_merge_budget import maximum_feedback, merge_prompt
from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.call_policy import retry_limit
from src.services.context_budget import provider_parameters
from src.services.invocation_types import StructuredRequest
from src.services.lesson_snapshots import canonical_hash
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.utils.cache import load_prompt_template

ESTIMATOR_VERSION = "utf8-v1-s4-20pct"
FORMULA = "ceil(serialized_utf8_bytes * 1.2); B = min(I, C - R); output = 1200 + 160*T + 120*S + thinking"


def analysis_prompt(snapshot, messages, *, owned_ids=None, reference_ids=None):
    inputs = dict(
        messages=[
            {k: m[k] for k in ("id", "role", "content")} for m in messages
        ],
        scenario=snapshot.scenario_context.model_dump(),
        problem=snapshot.config.problem.model_dump(),
        student=snapshot.config.student.model_dump(
            exclude={"resolved_model_config"}
        ),
        analysis=snapshot.config.analysis.model_dump(
            exclude={"resolved_model_config"}
        ),
    )
    if owned_ids is not None:
        inputs.update(
            owned_message_ids=owned_ids,
            reference_message_ids=reference_ids or [],
        )
    return (
        load_prompt_template("analysis_v2.txt")
        + "\n입력 JSON\n"
        + json.dumps(inputs, ensure_ascii=False)
    )


def analysis_request(snapshot, prompt, schema=UnifiedAnalysisOutput):
    selection = snapshot.config.analysis.resolved_model_config
    return StructuredRequest(
        selection.provider,
        selection.model_id,
        "analysis",
        "",
        [{"role": "user", "content": prompt}],
        selection.options.model_dump(exclude_unset=True),
        "plan",
        schema,
    )


def estimate_request(request):
    serialized = json.dumps(
        provider_parameters(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return math.ceil(len(serialized.encode("utf-8")) * 1.2)


def thinking_reserve(provider, options, definition, cap):
    if provider == "openai":
        effort = options.get("reasoning", {}).get(
            "effort", definition["default_reasoning_effort"]
        )
        return 0 if effort == "none" else math.ceil(cap / 2)
    thinking = options.get("thinking", {})
    if provider == "anthropic":
        mode = thinking.get("type", "disabled")
        return (
            thinking["budget_tokens"]
            if mode == "enabled"
            else math.ceil(cap / 2) if mode == "adaptive" else 0
        )
    budget = thinking.get("budget", -1)
    return math.ceil(cap / 2) if budget == -1 else budget


def build_plan(snapshot, messages, *, regenerate=False, catalog=None):
    selection = snapshot.config.analysis.resolved_model_config
    options = (
        selection.options.model_dump(exclude_unset=True) if selection else {}
    )
    definition = (
        capabilities(selection.provider, selection.model_id)
        if selection
        else None
    )
    plan = dict(
        version=1,
        chunk_policy_version="s4-turn-v1",
        regenerate=regenerate,
        mode="single",
        status="single",
        schema_version=2,
        contract_version=ROLE_CONTRACT_VERSIONS["analysis"],
        prompt_version=hashlib.sha256(
            load_prompt_template("analysis_v2.txt").encode("utf-8")
        ).hexdigest(),
        merge_prompt_version=hashlib.sha256(
            load_prompt_template("analysis_merge_v2.txt").encode("utf-8")
        ).hexdigest(),
        estimator_version=ESTIMATOR_VERSION,
        formula=FORMULA,
        input_hash=canonical_hash(messages),
        config_hash=canonical_hash(snapshot.model_dump()),
        message_ids=[m["id"] for m in messages],
        chunks=[],
        generation_calls=int(bool(messages)),
        retry_limit=retry_limit("analysis_unified", "analysis", False),
        estimated_input_tokens=None,
        estimated_output_tokens=None,
        reserved_thinking_tokens=None,
        input_budget_tokens=None,
        model_options=options,
        limits=definition,
        blocked_code=None,
        catalog_metadata=[
            item
            for item in (catalog or [])
            if selection and item.get("model_id") == selection.model_id
        ],
    )
    cap = options.get(
        "max_output_tokens",
        1024 if selection and selection.provider == "anthropic" else None,
    )
    code = None
    if definition is None or not any(
        definition.get(k)
        for k in ("input_token_limit", "combined_context_tokens")
    ):
        code = "unknown_limits"
    elif metadata_conflict(
        selection.model_id,
        SimpleNamespace(
            provider=selection.provider, catalog_models_json=catalog or []
        ),
    ):
        code = "catalog_limit_conflict"
    elif cap is None:
        code = "output_limit_missing"
    elif cap > definition["max_output_tokens"]:
        code = "output_limit_exceeded"
    else:
        reserve = thinking_reserve(selection.provider, options, definition, cap)
        budgets = [
            (
                definition[k] - cap
                if k == "combined_context_tokens"
                else definition[k]
            )
            for k in ("input_token_limit", "combined_context_tokens")
            if definition.get(k) is not None
        ]
        plan.update(
            frozen_output_cap=cap,
            reserved_thinking_tokens=reserve,
            input_budget_tokens=min(budgets),
            estimated_input_tokens=estimate_request(
                analysis_request(snapshot, analysis_prompt(snapshot, messages))
            ),
            estimated_output_tokens=1200
            + sum(160 if m["role"] == "teacher" else 120 for m in messages)
            + reserve,
        )
        if messages and (
            plan["estimated_output_tokens"] > cap
            or plan["estimated_input_tokens"] > min(budgets)
        ):
            code = chunk_plan(snapshot, messages, plan)
    if code:
        plan.update(
            mode="blocked",
            status="blocked",
            blocked_code=code,
            generation_calls=0,
        )
    plan["plan_hash"] = canonical_hash(plan)
    return plan


def complete_units(messages):
    """Attach unmatched messages without inventing stored turn links."""
    units, prefix = [], []
    index = 0
    while index < len(messages):
        teacher = messages[index]
        student = messages[index + 1] if index + 1 < len(messages) else None
        if (
            teacher["role"] == "teacher"
            and student is not None
            and student["role"] == "student"
            and teacher.get("turn_id") == student.get("turn_id")
        ):
            pair = [teacher, student]
            units.append(dict(messages=prefix + pair, pair=pair))
            prefix = []
            index += 2
        else:
            (units[-1]["messages"] if units else prefix).append(teacher)
            index += 1
    return units or [dict(messages=prefix, pair=[])]


def chunk_plan(snapshot, messages, plan):
    units = complete_units(messages)
    chunks, owned, reference = [], [], []

    def candidate(owned, reference):
        owned_ids = [m["id"] for m in owned]
        reference_ids = [m["id"] for m in reference]
        prompt = analysis_prompt(
            snapshot,
            reference + owned,
            owned_ids=owned_ids,
            reference_ids=reference_ids,
        )
        return dict(
            message_ids=owned_ids,
            reference_message_ids=reference_ids,
            estimated_input_tokens=estimate_request(
                analysis_request(snapshot, prompt)
            ),
            estimated_output_tokens=1200
            + sum(160 if m["role"] == "teacher" else 120 for m in owned)
            + plan["reserved_thinking_tokens"],
        )

    def fits(chunk):
        return (
            chunk["estimated_input_tokens"] <= plan["input_budget_tokens"]
            and chunk["estimated_output_tokens"] <= plan["frozen_output_cap"]
        )

    for index, unit in enumerate(units):
        proposed = candidate(owned + unit["messages"], reference)
        if not fits(proposed):
            if not owned:
                return "unit_too_large"
            chunks.append(candidate(owned, reference))
            if len(chunks) == 8:
                return "too_many_chunks"
            reference = units[index - 1]["pair"]
            owned = []
            if not fits(candidate(unit["messages"], reference)):
                return "unit_too_large"
        owned += unit["messages"]
    chunks.append(candidate(owned, reference))
    plan.update(
        chunks=chunks,
        mode="chunked",
        status="chunked",
        generation_calls=len(chunks) + 1,
    )
    ids = plan["message_ids"]
    rubric = snapshot.config.analysis.rubric
    bound = maximum_feedback(messages)
    statistics = (
        {r.id: len(ids) for r in rubric}
        if snapshot.config.analysis.classification_enabled
        else {}
    )
    coverage = dict(
        total_teacher=len(ids),
        classified_teacher=len(ids),
        non_analyzable_teacher=len(ids),
        unclassified_teacher=len(ids),
        expected_message_ids=ids,
        owned_message_ids=ids,
        reviewed_message_ids=ids,
        classified_message_ids=ids,
        non_analyzable_message_ids=ids,
        unclassified_message_ids=ids,
        response_missing_message_ids=ids,
        omitted_message_ids=ids,
    )
    prompt = merge_prompt(snapshot, [bound] * len(chunks), statistics, coverage)
    merge = dict(
        estimated_input_tokens=estimate_request(
            analysis_request(snapshot, prompt, MergeAnalysisOutput)
        ),
        estimated_output_tokens=1200 + plan["reserved_thinking_tokens"],
    )
    plan["merge"] = merge
    if (
        merge["estimated_input_tokens"] > plan["input_budget_tokens"]
        or merge["estimated_output_tokens"] > plan["frozen_output_cap"]
    ):
        return "merge_too_large"
    plan.update(
        estimated_input_tokens=sum(c["estimated_input_tokens"] for c in chunks)
        + merge["estimated_input_tokens"],
        estimated_output_tokens=sum(
            c["estimated_output_tokens"] for c in chunks
        )
        + merge["estimated_output_tokens"],
    )
    return None


def public_plan(plan):
    """Teachers receive coverage and estimates, never frozen private settings."""
    fields = (
        "mode",
        "status",
        "message_ids",
        "chunks",
        "generation_calls",
        "retry_limit",
        "estimated_input_tokens",
        "estimated_output_tokens",
        "reserved_thinking_tokens",
        "input_budget_tokens",
        "frozen_output_cap",
        "estimator_version",
        "formula",
        "blocked_code",
        "plan_hash",
        "merge",
        "chunk_policy_version",
    )
    return {key: plan[key] for key in fields if key in plan}


async def load_plan(db, session, *, regenerate=False):
    from sqlalchemy import select

    from src.models import Message, ProviderConnection
    from src.services.lesson_snapshots import read_lesson_snapshot

    lesson = read_lesson_snapshot(session)
    messages = list(
        await db.scalars(
            select(Message)
            .where(
                Message.session_id == session.id,
                Message.role.in_(["teacher", "student"]),
            )
            .order_by(Message.created_at, Message.id)
        )
    )
    selection = lesson.config.analysis.resolved_model_config
    connection = (
        await db.get(ProviderConnection, selection.provider_connection_id)
        if selection
        else None
    )
    inputs = [
        dict(id=m.id, role=m.role, content=m.content, turn_id=m.turn_id)
        for m in messages
    ]
    return build_plan(
        lesson,
        inputs,
        regenerate=regenerate,
        catalog=connection.catalog_models_json if connection else [],
    )

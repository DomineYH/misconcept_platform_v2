"""Sequential execution of a confirmed, frozen analysis plan."""

from src.models.provider_connection import now
from src.services.analysis_invocations import AnalysisCaller
from src.services.analysis_merge_budget import merge_prompt
from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.analysis_plan import (
    analysis_prompt,
    analysis_request,
    estimate_request,
)
from src.services.analysis_statistics import analysis_statistics
from src.services.invocation_types import InvocationError
from src.services.lesson_snapshots import canonical_hash


def deduplicate_findings(findings):
    seen, unique = set(), []
    for finding in findings:
        key = (
            finding["kind"],
            tuple((e["message_id"], e["quote"]) for e in finding["evidence"]),
        )
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


async def run_chunk_pipeline(
    session_id,
    all_messages,
    teacher_messages,
    snapshot,
    factory,
    owner_id=None,
    actor_id=None,
    *,
    request_id=None,
    run_id=None,
    plan,
):
    assert len(plan["chunks"]) <= 8, "Analysis plans support at most 8 chunks"
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
    by_id = {m["id"]: m for m in messages}
    context = dict(
        messages=messages,
        labels={r.id: r.level for r in analysis.rubric},
        classification_enabled=analysis.classification_enabled,
    )
    chunks = [dict(**chunk, status="not_run") for chunk in plan["chunks"]]
    payload = dict(schema_version=2, message_classifications=[])
    feedback, reviewed = [], []
    status, code, merge_status = "ok", None, "not_run"
    estimates = None

    def budget(estimates):
        return dict(
            estimator=plan["estimator_version"],
            formula=plan["formula"],
            estimated_input_tokens=estimates["estimated_input_tokens"],
            estimated_output_tokens=estimates["estimated_output_tokens"],
            input_budget_tokens=plan["input_budget_tokens"],
            reserved_output_tokens=plan["frozen_output_cap"],
            reserved_thinking_tokens=plan["reserved_thinking_tokens"],
            capability_definition_version=plan["limits"]["definition_version"],
        )

    for chunk in chunks:
        inputs = [
            by_id[mid]
            for mid in chunk["reference_message_ids"] + chunk["message_ids"]
        ]
        validation = {
            **context,
            "messages": inputs,
            "owned_message_ids": chunk["message_ids"],
            "invalid_message_ids": [],
        }
        try:
            result, _ = await caller.structured(
                analysis_prompt(
                    snapshot,
                    inputs,
                    owned_ids=chunk["message_ids"],
                    reference_ids=chunk["reference_message_ids"],
                ),
                UnifiedAnalysisOutput,
                "analysis_chunk",
                validation_context=validation,
                context_budget=budget(chunk),
            )
        except InvocationError as error:
            chunk.update(status="failed", error_code=error.code)
            code = error.code
            break
        owned_messages = [
            m for m in all_messages if m.id in chunk["message_ids"]
        ]
        _, _, _, chunk_status, code = analysis_statistics(
            owned_messages,
            result,
            analysis,
            validation,
            "ok",
            None,
        )
        chunk.update(status=chunk_status, error_code=code)
        context.setdefault("analysis_errors", []).extend(
            validation.get("analysis_errors", [])
        )
        context.setdefault("invalid_message_ids", []).extend(
            validation.get("invalid_message_ids", [])
        )
        payload["message_classifications"].extend(
            result["message_classifications"]
        )
        result["misconception_findings"] = deduplicate_findings(
            result["misconception_findings"]
        )
        feedback.append(
            {k: result[k] for k in MergeAnalysisOutput.model_fields}
        )
        reviewed.extend(chunk["message_ids"])
        if chunk_status != "ok":
            break
    status = (
        "ok"
        if all(c["status"] == "ok" for c in chunks)
        else "degraded" if feedback else "failed"
    )
    for key in MergeAnalysisOutput.model_fields:
        if key != "schema_version":
            payload[key] = [item for part in feedback for item in part[key]]
    order = {m["id"]: i for i, m in enumerate(messages)}
    payload["message_classifications"].sort(
        key=lambda c: order[c["message_id"]]
    )
    distribution, questions, coverage, status, code = analysis_statistics(
        all_messages,
        payload,
        analysis,
        context,
        status,
        code,
    )
    coverage.update(chunks=chunks, reviewed_message_ids=reviewed)
    if status == "ok":
        prompt = merge_prompt(snapshot, feedback, distribution, coverage)
        estimates = dict(
            estimated_input_tokens=estimate_request(
                analysis_request(snapshot, prompt, MergeAnalysisOutput)
            ),
            estimated_output_tokens=plan["merge"]["estimated_output_tokens"],
        )
        evidence = []
        for part in feedback:
            for section in (
                "misconception_findings",
                "strengths",
                "improvements",
                "dialogue_coaching",
            ):
                for item in part[section]:
                    evidence.extend(
                        item["evidence"]
                        if section == "misconception_findings"
                        else [
                            dict(
                                message_id=item["message_id"],
                                quote=item["quote"],
                            )
                        ]
                    )
        validation = dict(**context, allowed_evidence=evidence)
        try:
            if (
                estimates["estimated_input_tokens"]
                > plan["input_budget_tokens"]
            ):
                raise InvocationError("context_limit")
            if estimates["estimated_output_tokens"] > plan["frozen_output_cap"]:
                raise InvocationError("output_limit")
            merged, _ = await caller.structured(
                prompt,
                MergeAnalysisOutput,
                "analysis_merge",
                validation_context=validation,
                context_budget=budget(estimates),
            )
            _, _, _, merge_status, code = analysis_statistics(
                all_messages,
                payload,
                analysis,
                validation,
                "ok",
                None,
            )
            if merge_status == "ok":
                merged["misconception_findings"] = deduplicate_findings(
                    merged["misconception_findings"]
                )
                payload.update(merged)
            else:
                status = "degraded"
        except InvocationError as error:
            status, merge_status, code = "degraded", "failed", error.code
    if status != "ok":
        payload["brief_feedback"] = [
            (
                f"부분 분석: 검증된 {len(reviewed)} / {len(messages)}개 메시지의 결과입니다. 완료·실패·미실행 범위는 분석 범위에서 확인하세요."
                if feedback
                else "분석에 실패했습니다. 검증된 분할 결과가 없습니다."
            )
        ]
    payload["metadata"] = dict(
        coverage=coverage,
        mode="chunked",
        estimator_version=plan["estimator_version"],
        formula=plan["formula"],
        plan_hash=plan["plan_hash"],
        merge_status=merge_status,
        merge_estimates=estimates,
        request_id=caller.request_id,
        run_id=run_id,
        config_hash=canonical_hash(snapshot.model_dump()),
        prompt_version=plan["prompt_version"],
        merge_prompt_version=plan["merge_prompt_version"],
        schema_version=2,
        generated_at=now().isoformat(),
        error_code=code,
        outcome=status,
    )
    return (
        distribution,
        questions,
        payload,
        status,
        selection.model_id,
        plan["prompt_version"],
        [],
    )

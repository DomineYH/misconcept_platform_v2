"""The final synthesis can only use validated chunk evidence and fit its budget."""

import json

import httpx2
import pytest
from s4_analysis_fixtures import prompt_inputs
from sqlalchemy import select
from test_analysis_chunks import chunk_reply, confirm
from test_analysis_invocations import USAGE, analysis_transport
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_student_probe import response_body

from src.models import ApiUsageLog, Message

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize(
    "failure,code",
    [
        ("quote", "invalid_reference"),
        ("id", "invalid_reference"),
        ("classification", "invalid_output"),
        ("envelope", "invalid_output"),
        ("json", "invalid_json"),
        ("empty", "empty_response"),
        ("length", "output_limit"),
        ("refused", "refused"),
    ],
)
async def test_merge_failure_keeps_verified_chunks_as_partial_analysis(
    data, api, monkeypatch, failure, code
):
    await long_session(data, monkeypatch)

    async def upstream(request, body):
        inputs = prompt_inputs(body)
        value = chunk_reply(body)
        if body["text"]["format"]["name"] == "UnifiedAnalysisOutput":
            teacher = next(
                m
                for m in inputs["messages"]
                if m["role"] == "teacher"
                and m["id"] in inputs["owned_message_ids"]
            )
            value["strengths"] = [
                dict(
                    message_id=teacher["id"],
                    quote="발화",
                    reason="검증된 중간 근거",
                )
            ]
        else:
            if failure == "quote":
                value["strengths"][0]["quote"] = "발화 1"
            elif failure == "id":
                value["strengths"][0]["message_id"] = prompt_inputs(calls[0])[
                    "messages"
                ][2]["id"]
            elif failure == "classification":
                value["message_classifications"] = []
            elif failure == "envelope":
                del value["strengths"]
            elif failure == "json":
                return httpx2.Response(
                    200, json=response_body("{PRIVATE PARTIAL JSON", USAGE)
                )
            elif failure == "empty":
                value["brief_feedback"] = []
            else:
                response = response_body(json.dumps(value), USAGE)
                response.update(
                    status="incomplete",
                    incomplete_details=dict(
                        reason=(
                            "content_filter"
                            if failure == "refused"
                            else "max_output_tokens"
                        )
                    ),
                )
                return httpx2.Response(200, json=response)
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    assert result["latest_run"]["status"] == "degraded"
    assert result["latest_run"]["error_code"] == code
    report = result["accepted_report"]
    assert (
        report["status"] == "degraded"
        and "부분 분석" in report["brief_feedback"][0]
    )
    assert report["coverage"]["reviewed_message_ids"] == plan["message_ids"]
    assert [c["status"] for c in report["coverage"]["chunks"]] == ["ok", "ok"]
    assert [s["message_id"] for s in report["strengths"]] == [
        c["message_ids"][0] for c in plan["chunks"]
    ]
    assert [s["quote"] for s in report["strengths"]] == ["발화", "발화"]
    assert len(report["message_classifications"]) == 5 and len(calls) == 3
    assert "PRIVATE" not in json.dumps(result)
    async with data.factory() as db:
        ledger = list(
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        )
    assert [(r.operation, r.status) for r in ledger[:2]] == [
        ("analysis_chunk", "completed")
    ] * 2
    assert (
        ledger[-1].operation == "analysis_merge"
        and ledger[-1].error_code == code
    )
    assert ledger[-1].status == (
        "refused" if failure == "refused" else "failed"
    )
    assert all(c.is_closed() for c in clients)


@pytest.mark.parametrize(
    "ceiling,code", [("input", "context_limit"), ("output", "output_limit")]
)
async def test_executor_checks_real_merge_serialization_before_admission(
    data, api, monkeypatch, ceiling, code
):
    from src.services.analysis_chunks import run_chunk_pipeline
    from src.services.analysis_plan import load_plan
    from src.services.lesson_snapshots import read_lesson_snapshot

    await long_session(data, monkeypatch)
    messages = list(
        await data.db.scalars(
            select(Message).order_by(Message.created_at, Message.id)
        )
    )
    plan = await load_plan(data.db, data.session)
    # Exercise the executor's public budget boundary with a supplied ceiling.
    plan[
        "input_budget_tokens" if ceiling == "input" else "frozen_output_cap"
    ] = 1
    snapshot = read_lesson_snapshot(data.session)
    await data.db.commit()

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    result = await run_chunk_pipeline(
        data.session.id,
        messages,
        [m for m in messages if m.role == "teacher"],
        snapshot,
        data.factory,
        data.owner.id,
        data.owner.id,
        plan=plan,
    )
    assert (
        result[3] == "degraded" and result[2]["metadata"]["error_code"] == code
    )
    assert (
        result[2]["metadata"]["merge_estimates"]["estimated_input_tokens"] > 1
    )
    assert len(calls) == 2 and all(
        c["text"]["format"]["name"] == "UnifiedAnalysisOutput" for c in calls
    )

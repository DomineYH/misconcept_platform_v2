"""Confirmed chunk execution through HTTP, SQLite and the mocked SDK seam."""

import json
from uuid import uuid4

import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply, prompt_inputs
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_student_probe import response_body

from src.models import ApiUsageLog

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def chunk_reply(body):
    inputs = prompt_inputs(body)
    if body["text"]["format"]["name"] == "MergeAnalysisOutput":
        return inputs["chunk_feedback"][0]
    owned = set(inputs["owned_message_ids"])
    return analysis_reply(
        [m for m in inputs["messages"] if m["id"] in owned],
        enabled=inputs["analysis"]["classification_enabled"],
    )


async def confirm(api, session_id, *, admin=False):
    url = (
        f"/admin/sessions/{session_id}/analyze_regenerate"
        if admin
        else f"/sessions/{session_id}/analyze"
    )
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    planned = await api.post(
        url, json=dict(request_id=str(uuid4())), headers=headers
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()["plan"]
    body = dict(request_id=str(uuid4()), plan_hash=plan["plan_hash"])
    accepted = await api.post(url, json=body, headers=headers)
    assert accepted.status_code == 202, accepted.text
    return accepted.json(), plan, body


async def test_confirmed_chunks_merge_once_with_unique_ordered_classifications_and_ledger(
    data, api, monkeypatch
):
    await long_session(data, monkeypatch)

    async def upstream(request, body):
        value = chunk_reply(body)
        if "message_classifications" in value:
            value["message_classifications"].reverse()
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, body = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    report = result["accepted_report"]
    assert result["latest_run"]["status"] == report["status"] == "ok"
    assert len(calls) == plan["generation_calls"] == 3
    for call, chunk in zip(calls, plan["chunks"]):
        inputs = prompt_inputs(call)
        assert inputs["owned_message_ids"] == chunk["message_ids"]
        assert inputs["reference_message_ids"] == chunk["reference_message_ids"]
        assert [m["id"] for m in inputs["messages"]] == chunk[
            "reference_message_ids"
        ] + chunk["message_ids"]
    merge_input = prompt_inputs(calls[-1])
    assert "messages" not in merge_input
    assert all(
        "message_classifications" not in p
        for p in merge_input["chunk_feedback"]
    )
    teacher_ids = [
        m["id"] for m in result["messages"] if m["role"] == "teacher"
    ]
    assert [
        c["message_id"] for c in report["message_classifications"]
    ] == teacher_ids
    assert report["coverage"]["reviewed_message_ids"] == plan["message_ids"]
    assert [c["status"] for c in report["coverage"]["chunks"]] == ["ok", "ok"]
    assert sum(d["count"] for d in report["distribution"]) == 5
    assert all(
        c["max_output_tokens"] == 4000
        and c["reasoning"] == {"effort": "medium"}
        for c in calls
    )
    replay = await api.post(
        f"/sessions/{data.session.id}/analyze",
        json=body,
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert replay.json()["run_id"] == accepted["run_id"] and len(calls) == 3
    async with data.factory() as db:
        ledger = list(
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        )
    assert [(r.operation, r.attempt_no, r.status) for r in ledger] == [
        ("analysis_chunk", 1, "completed"),
        ("analysis_chunk", 1, "completed"),
        ("analysis_merge", 1, "completed"),
    ]
    assert len({r.invocation_id for r in ledger}) == 3
    assert all(
        r.request_id == body["request_id"]
        and r.run_id == accepted["run_id"]
        and r.total_tokens == 15
        for r in ledger
    )
    assert all(c.is_closed() for c in clients)


@pytest.mark.parametrize(
    "stop_at,error",
    [
        (1, "refused"),
        (2, "refused"),
        (2, "output_limit"),
        (2, "context_limit"),
        (2, "invalid_reference"),
        (2, "unclassified"),
    ],
)
async def test_first_non_ok_chunk_stops_calls_and_preserves_only_validated_range(
    data, api, monkeypatch, stop_at, error
):
    from src.models import Message

    await long_session(data, monkeypatch)
    for i in range(10, 12):
        data.db.add(
            Message(
                session_id=data.session.id,
                role="teacher" if i % 2 else "student",
                content=f"발화 {i}",
                turn_id=f"turn-{(i-1)//2}",
            )
        )
    await data.db.commit()

    async def upstream(request, body):
        value = chunk_reply(body)
        if len(calls) == stop_at:
            if error in {"refused", "output_limit"}:
                response = response_body(json.dumps(value), USAGE)
                response.update(
                    status="incomplete",
                    incomplete_details=dict(
                        reason=(
                            "content_filter"
                            if error == "refused"
                            else "max_output_tokens"
                        )
                    ),
                )
                return httpx2.Response(200, json=response)
            if error == "context_limit":
                return httpx2.Response(
                    400,
                    json=dict(
                        error=dict(
                            code="context_length_exceeded",
                            message="PRIVATE ERROR",
                        )
                    ),
                )
            if error == "invalid_reference":
                value["message_classifications"][0][
                    "quote"
                ] = "PRIVATE INVALID QUOTE"
            else:
                value["message_classifications"][0].update(
                    disposition="unclassified", rubric_id=None
                )
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    assert len(plan["chunks"]) == 3 and len(calls) == stop_at
    assert all(
        c["text"]["format"]["name"] == "UnifiedAnalysisOutput" for c in calls
    )
    usable = stop_at > 1 or error in {"invalid_reference", "unclassified"}
    assert result["latest_run"]["status"] == (
        "degraded" if usable else "failed"
    )
    assert result["latest_run"]["error_code"] == (
        None if error == "unclassified" else error
    )
    if usable:
        report = result["accepted_report"]
        reviewed_chunks = (
            stop_at
            if error in {"invalid_reference", "unclassified"}
            else stop_at - 1
        )
        reviewed_ids = [
            mid
            for chunk in plan["chunks"][:reviewed_chunks]
            for mid in chunk["message_ids"]
        ]
        assert report["coverage"]["reviewed_message_ids"] == reviewed_ids
        assert "부분 분석" in report["brief_feedback"][0]
        assert str(len(reviewed_ids)) in report["brief_feedback"][0]
        assert [c["status"] for c in report["coverage"]["chunks"]] == ["ok"] * (
            stop_at - 1
        ) + [
            (
                "degraded"
                if error in {"invalid_reference", "unclassified"}
                else "failed"
            )
        ] + [
            "not_run"
        ] * (
            3 - stop_at
        )
        assert all(
            c["message_id"] in reviewed_ids
            for c in report["message_classifications"]
        )
    else:
        assert result["accepted_report"] is None
    assert "PRIVATE" not in json.dumps(result)
    assert all(c.is_closed() for c in clients)


@pytest.mark.parametrize("enabled", [True, False])
async def test_eight_confirmed_chunks_make_nine_calls_with_classification_on_or_off(
    data, api, monkeypatch, enabled
):
    import copy

    from sqlalchemy import delete

    from src.models import Message
    from src.services import analysis_plan
    from src.services.lesson_snapshots import canonical_hash
    from src.services.model_capabilities import capabilities

    await long_session(data, monkeypatch)
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    analysis = envelope["config"]["analysis"]
    analysis["resolved_model_config"]["options"]["max_output_tokens"] = 3000
    analysis["classification_enabled"] = enabled
    if not enabled:
        analysis.update(rubric=[], rubric_name="")
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.execute(delete(Message))
    for i in range(1, 17):
        data.db.add(
            Message(
                session_id=data.session.id,
                role="teacher" if i % 2 else "student",
                content="x",
                turn_id=f"turn-{(i-1)//2}",
            )
        )
    await data.db.commit()
    definition = capabilities("openai", "gpt-5-mini")
    # A synthetic ceiling makes the eight-chunk boundary reachable in mocks.
    definition["combined_context_tokens"] = 1000000
    monkeypatch.setattr(analysis_plan, "capabilities", lambda *_: definition)

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    report = result["accepted_report"]
    assert result["latest_run"]["status"] == "ok"
    assert (
        len(plan["chunks"]) == 8 and len(calls) == plan["generation_calls"] == 9
    )
    assert [c["status"] for c in report["coverage"]["chunks"]] == ["ok"] * 8
    assert report["coverage"]["reviewed_message_ids"] == plan["message_ids"]
    assert len(report["message_classifications"]) == (8 if enabled else 0)
    assert report["classification_enabled"] is enabled
    if not enabled:
        assert report["distribution"] == []
    assert all(
        c["max_output_tokens"] == 3000
        and c["reasoning"] == {"effort": "medium"}
        for c in calls
    )


async def test_executor_rejects_more_than_eight_chunks_before_any_call(
    data, api, monkeypatch
):
    from src.models import Message
    from src.services.analysis_chunks import run_chunk_pipeline
    from src.services.analysis_plan import load_plan
    from src.services.lesson_snapshots import read_lesson_snapshot

    await long_session(data, monkeypatch)
    plan = await load_plan(data.db, data.session)
    plan["chunks"] = (plan["chunks"] * 5)[:9]
    messages = list(await data.db.scalars(select(Message).order_by(Message.id)))
    snapshot = read_lesson_snapshot(data.session)
    await data.db.commit()

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    with pytest.raises(AssertionError, match="at most 8 chunks"):
        await run_chunk_pipeline(
            data.session.id,
            messages,
            [m for m in messages if m.role == "teacher"],
            snapshot,
            data.factory,
            data.owner.id,
            plan=plan,
        )
    assert calls == []

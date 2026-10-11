"""Validated analysis completion, independent failure and cancellation over ASGI."""

import asyncio
import copy
import json
from uuid import uuid4

import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply, prompt_inputs
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport
from test_comparison_analysis import (
    analysis_comparison,
    client,
    comparison,
    scenario_payload,
    student,
)
from test_comparison_streaming import live_comparison, next_event
from test_scenario_api import login
from test_student_probe import response_body

from src.models import ApiUsageLog, GenerationRun
from src.services.call_admission import active_calls, registered_calls
from src.services.generation_lifecycle import active_runs
from src.services.generation_runs import digest

__all__ = [
    "analysis_comparison",
    "client",
    "comparison",
    "scenario_payload",
    "student",
]


@pytest.mark.parametrize("stop", ["panel", "disconnect"])
async def test_analysis_fast_success_and_failed_panel_survive_sibling_cancellation(
    data, client, analysis_comparison, monkeypatch, stop
):
    body = analysis_comparison
    body["panels"] = [
        {**copy.deepcopy(body["panels"][0]), "panel_id": str(i + 1)}
        for i in range(3)
    ]
    for i, panel in enumerate(body["panels"]):
        panel["model_config"]["options"]["max_output_tokens"] = 8192 + i
    entered, closed = asyncio.Event(), asyncio.Event()

    async def upstream(request, payload):
        if payload["max_output_tokens"] == 8193:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        await entered.wait()
        reply = analysis_reply(prompt_inputs(payload)["messages"])
        return httpx2.Response(
            200,
            json=response_body(
                (
                    json.dumps(reply)
                    if payload["max_output_tokens"] == 8192
                    else "INVALID_JSON"
                ),
                USAGE,
            ),
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.admin)
    async with live_comparison(client, body) as live:
        completed = await next_event(live, "output.completed", "1")
        assert completed["seq"] == 1 and completed["outcome_status"] == "ok"
        path = f"/admin/comparisons/{completed['comparison_run_id']}"
        state = (await client.get(path)).json()
        assert state["panels"][0]["status"] == "ok"
        assert state["panels"][1]["status"] == "running"
        if stop == "panel":
            response = await client.post(
                f"{path}/panels/2/stop",
                json=dict(generation_id=state["panels"][1]["run_id"]),
            )
            assert response.status_code == 200
            terminal = await next_event(live, "run.cancelled", "2")
            assert terminal["result"] is None
            await asyncio.wait_for(live.task, 10)
        else:
            # Wait for the independent failed terminal before disconnecting.
            if state["panels"][2]["status"] == "running":
                await next_event(live, "run.failed", "3")
    assert closed.is_set() and len(calls) == 3
    state = (await client.get(path)).json()
    assert [panel["status"] for panel in state["panels"]] == [
        "ok",
        "cancelled" if stop == "panel" else "interrupted",
        "failed",
    ]
    assert state["status"] == "finished"
    assert state["panels"][0]["result"] == completed["result"]
    assert not active_calls and not registered_calls and not active_runs
    assert all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = list(await db.scalars(select(ApiUsageLog)))
        assert len(rows) == 3 and all(
            row.attempt_no == 1 and row.status != "running" for row in rows
        )


@pytest.mark.parametrize("initial_status", ["degraded", "failed"])
async def test_analysis_retry_preserves_frozen_conditions_and_prior_result(
    data, client, student, analysis_comparison, monkeypatch, initial_status
):
    from test_comparison_student import classroom_counts
    from test_student_generation import frames

    body = analysis_comparison
    body["panels"].append({**copy.deepcopy(body["panels"][0]), "panel_id": "2"})
    body["panels"][1]["model_config"]["options"]["max_output_tokens"] = 9000
    counts = await classroom_counts(data.db)
    retrying = False

    async def upstream(request, payload):
        reply = analysis_reply(prompt_inputs(payload)["messages"])
        if not retrying and payload["max_output_tokens"] == 9000:
            reply["message_classifications"].pop(0)
            content = (
                json.dumps(reply)
                if initial_status == "degraded"
                else "INVALID_JSON"
            )
        else:
            content = json.dumps(reply)
        return httpx2.Response(200, json=response_body(content, USAGE))

    clients, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=body)
    assert response.status_code == 200, response.text
    path = f"/admin/comparisons/{frames(response)[0][1]['comparison_run_id']}"
    before = (await client.get(path)).json()
    assert [panel["status"] for panel in before["panels"]] == [
        "ok",
        initial_status,
    ]
    old = before["panels"][1]
    assert len(calls) == 2
    original_call = next(
        call for call in calls if call["max_output_tokens"] == 9000
    )
    student.model.default_options_json = {"max_output_tokens": 2000}
    await data.db.commit()
    retrying = True
    retry = dict(request_id=str(uuid4()), generation_id=old["run_id"])
    response = await client.post(f"{path}/panels/2/retry", json=retry)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[0][1]["request_id"] == retry["request_id"]
    assert all(value["panel_id"] == "2" for _, value in events)
    after = (await client.get(path)).json()
    assert after["status"] == "finished"
    assert after["panels"][0] == before["panels"][0]
    latest = after["panels"][1]
    assert latest["status"] == latest["outcome_status"] == "ok"
    assert latest["run_id"] != old["run_id"]
    assert latest["turn_id"] == old["turn_id"]
    assert latest["attempts"] == [old]
    assert latest["result"] == events[-1][1]["result"]
    for key in (
        "source_snapshot",
        "source_hash",
        "context_hash",
        "canonical_context",
        "current_message",
        "created_at",
        "expires_at",
        "request_id",
    ):
        assert after[key] == before[key]
    assert len(calls) == 3 and calls[-1] == original_call
    assert (
        await client.post(f"{path}/panels/2/retry", json=retry)
    ).json() == after
    assert len(calls) == 3 and all(c.is_closed() for c in clients)
    assert not active_calls and not registered_calls and not active_runs
    async with data.factory() as db:
        run = await db.get(GenerationRun, latest["run_id"])
        assert run.request_id == retry["request_id"]
        assert run.input_hash == digest(retry)
        assert run.operation == "analysis" and run.session_id is None
        rows = list(await db.scalars(select(ApiUsageLog)))
        assert len(rows) == 3 and all(
            row.attempt_no == 1
            and row.usage_scope == "comparison"
            and row.session_id is None
            for row in rows
        )
        assert await classroom_counts(db) == counts

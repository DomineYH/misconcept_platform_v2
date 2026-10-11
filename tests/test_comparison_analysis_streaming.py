"""Validated analysis completion, independent failure and cancellation over ASGI."""

import asyncio
import copy
import json

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

from src.models import ApiUsageLog
from src.services.call_admission import active_calls, registered_calls
from src.services.generation_lifecycle import active_runs

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

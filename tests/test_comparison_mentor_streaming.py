"""Final-only mentor SSE and independent child cancellation at ASGI boundaries."""

import asyncio
from types import SimpleNamespace

import pytest
from lesson_fixtures import mentor_output
from sqlalchemy import select
from test_comparison_mentor import mentor_comparison
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog

__all__ = [
    "client",
    "comparison",
    "scenario_payload",
    "student",
    "mentor_comparison",
]


@pytest.mark.parametrize("action", ["stop", "disconnect", "finish"])
async def test_mentor_panels_finish_independently_and_preserve_committed_results(
    data, client, student, mentor_comparison, action, monkeypatch
):
    from test_comparison_concurrency import add_panels
    from test_comparison_streaming import live_comparison, next_event

    from src.services import comparison_stream
    from src.services.call_admission import active_calls, registered_calls
    from src.services.generation_lifecycle import active_runs

    login(client, data.admin)
    add_panels(mentor_comparison, 3)
    for index, panel in enumerate(mentor_comparison["panels"]):
        panel["model_config"]["options"]["max_output_tokens"] = 1500 + index
    opened, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(comparison_stream, "HEARTBEAT_SECONDS", 0.01)

    async def create(**kwargs):
        budget = kwargs["max_output_tokens"]
        if budget == 1502:
            opened.set()
            await release.wait()
        raw = (
            '{"PRIVATE PARTIAL":'
            if budget == 1501
            else mentor_output("Mentor coaching")
        )
        return SimpleNamespace(output_text=raw, usage=None)

    student.responses.create.side_effect = create
    async with live_comparison(client, mentor_comparison) as live:
        terminals, accepted = {}, []
        while len(terminals) < 2:
            kind, value = await asyncio.wait_for(live.received.get(), 10)
            assert kind != "output.delta"
            if kind == "run.accepted":
                accepted.append(value)
            elif kind in ("output.completed", "run.failed"):
                terminals[value["panel_id"]] = kind
        assert terminals == {"1": "output.completed", "2": "run.failed"}
        await asyncio.wait_for(opened.wait(), 10)
        assert len({value["context_hash"] for value in accepted}) == 1
        path = f"/admin/comparisons/{accepted[0]['comparison_run_id']}"
        state = (await client.get(path)).json()
        assert [panel["status"] for panel in state["panels"]] == [
            "completed",
            "failed",
            "running",
        ]
        assert state["panels"][1]["result"] is None
        assert state["panels"][2]["result"] is None
        assert len(active_calls) == 1 and next(iter(active_calls)).admin
        calls = [
            call.kwargs for call in student.responses.create.call_args_list
        ]
        assert len(calls) == 3
        assert all(
            call["instructions"] == calls[0]["instructions"]
            and call["input"] == calls[0]["input"]
            and "stream" not in call
            for call in calls
        )
        if action == "stop":
            last = state["panels"][2]
            result = await client.post(
                f"{path}/panels/3/stop", json={"generation_id": last["run_id"]}
            )
            assert result.status_code == 200
            await next_event(live, "run.cancelled", "3")
        elif action == "disconnect":
            live.disconnect.set()
        else:
            release.set()
            await next_event(live, "output.completed", "3")
        await asyncio.wait_for(live.task, 10)
        final = (await client.get(path)).json()
        assert final["status"] == "finished"
        assert final["panels"][:2] == state["panels"][:2]
        assert (
            final["panels"][2]["status"]
            == {
                "stop": "cancelled",
                "disconnect": "interrupted",
                "finish": "completed",
            }[action]
        )
    assert (
        student.responses.create.await_count == student.close.await_count == 3
    )
    assert not active_calls and not registered_calls and not active_runs
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
        assert len(ledger) == 3 and all(
            entry.status != "running" for entry in ledger
        )


async def test_mentor_heartbeat_precedes_final_without_reason_or_raw_json(
    data, client, student, mentor_comparison, monkeypatch
):
    from src.services import comparison_stream

    login(client, data.admin)
    monkeypatch.setattr(comparison_stream, "HEARTBEAT_SECONDS", 0.01)

    async def create(**kwargs):
        await asyncio.sleep(0.06)
        return SimpleNamespace(
            output_text=mentor_output("Heartbeat coaching"), usage=None
        )

    student.responses.create.side_effect = create
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == 200, response.text
    assert ": ping\n\n" in response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert [value["seq"] for _, value in events] == [0, 1]
    assert (
        "PRIVATE-REASON" not in response.text
        and '"reason_summary"' not in response.text
    )

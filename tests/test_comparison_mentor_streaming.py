"""Final-only mentor SSE and independent child cancellation at ASGI boundaries."""

import asyncio
import copy
from types import SimpleNamespace
from uuid import uuid4

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

from src.models import ApiUsageLog, GenerationRun, ModelConfig, Session

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


@pytest.mark.parametrize("mode", ["manual", "auto"])
@pytest.mark.parametrize(
    "initial_status", ["failed", "cancelled", "interrupted"]
)
async def test_mentor_retry_preserves_frozen_conditions_and_prior_result(
    data, client, student, mentor_comparison, mode, initial_status
):
    from test_comparison_streaming import live_comparison, next_event
    from test_comparison_student import classroom_counts

    from src.services.call_admission import active_calls, registered_calls
    from src.services.generation_lifecycle import active_runs
    from src.services.generation_runs import digest

    body = mentor_comparison
    body["source_snapshot"]["config"]["mentor"]["mode"] = mode
    body["panels"].append({**copy.deepcopy(body["panels"][0]), "panel_id": "2"})
    body["panels"][1]["model_config"]["options"]["max_output_tokens"] = 1501
    counts = await classroom_counts(data.db)
    counters = (
        data.session.tutor_question_count,
        data.session.tutor_intervention_count,
    )
    retrying = False
    entered = asyncio.Event()

    async def create(**kwargs):
        raw = mentor_output("Retry coaching" if mode == "manual" else None)
        if not retrying and kwargs["max_output_tokens"] == 1501:
            entered.set()
            if initial_status != "failed":
                await asyncio.Event().wait()
            raw = "INVALID_JSON"
        return SimpleNamespace(output_text=raw, usage=None)

    student.responses.create.side_effect = create
    login(client, data.admin)
    async with live_comparison(client, body) as live:
        completed = await next_event(live, "output.completed", "1")
        path = f"/admin/comparisons/{completed['comparison_run_id']}"
        await asyncio.wait_for(entered.wait(), 10)
        if initial_status == "failed":
            await asyncio.wait_for(live.task, 10)
        elif initial_status == "cancelled":
            state = (await client.get(path)).json()
            response = await client.post(
                f"{path}/panels/2/stop",
                json={"generation_id": state["panels"][1]["run_id"]},
            )
            assert response.status_code == 200
            await next_event(live, "run.cancelled", "2")
    before = (await client.get(path)).json()
    assert before["status"] == "finished"
    assert [panel["status"] for panel in before["panels"]] == [
        "completed",
        initial_status,
    ]
    old = before["panels"][1]
    assert old["result"] is None and old["text"] == ""
    assert student.responses.create.await_count == 2
    original_call = next(
        call.kwargs
        for call in student.responses.create.call_args_list
        if call.kwargs["max_output_tokens"] == 1501
    )
    model = await data.db.get(
        ModelConfig, body["panels"][1]["model_config"]["model_config_id"]
    )
    model.default_options_json = {"max_output_tokens": 2000}
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
    assert latest["status"] == "completed" and latest["run_id"] != old["run_id"]
    assert latest["turn_id"] == old["turn_id"]
    assert latest["attempts"] == [old]
    assert (
        latest["result"]
        == events[-1][1]["result"]
        == dict(
            should_intervene=mode == "manual",
            coaching="Retry coaching" if mode == "manual" else "",
        )
    )
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
    assert before["current_message_id"] is after["current_message_id"] is None
    assert student.responses.create.await_count == 3
    assert student.responses.create.call_args.kwargs == original_call
    assert "stream" not in original_call
    assert "PRIVATE-REASON" not in response.text + str(after)
    assert (
        await client.post(f"{path}/panels/2/retry", json=retry)
    ).json() == after
    assert (
        student.responses.create.await_count == student.close.await_count == 3
    )
    assert not active_calls and not registered_calls and not active_runs
    async with data.factory() as db:
        run = await db.get(GenerationRun, latest["run_id"])
        assert run.request_id == retry[
            "request_id"
        ] and run.input_hash == digest(retry)
        assert run.operation == "mentor" and run.session_id is None
        assert (
            run.mentor_trigger == mode
            and run.mentor_reason_summary == "PRIVATE-REASON"
        )
        rows = list(await db.scalars(select(ApiUsageLog)))
        assert len(rows) == 3 and all(
            row.attempt_no == 1
            and row.usage_scope == "comparison"
            and row.session_id is None
            and row.operation == "comparison_mentor"
            for row in rows
        )
        assert await classroom_counts(db) == counts
        session = await db.get(Session, data.session.id)
        assert (
            session.tutor_question_count,
            session.tutor_intervention_count,
        ) == counters

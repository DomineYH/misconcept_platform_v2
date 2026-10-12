"""Additive S6 independence/fault samples using the existing synthetic seams."""

import asyncio
import copy
from time import monotonic_ns
from types import SimpleNamespace
from uuid import uuid4

import pytest
from lesson_fixtures import mentor_output
from s6_measurement import write_sample
from s6_observer import Observation, input_metadata
from sqlalchemy import select
from test_comparison_concurrency import (
    test_admitted_panel_failure_does_not_cancel_or_retry_siblings as panel_failure,
)
from test_comparison_student import comparison
from test_mentor_generation import complete_turn, mentor, mentor_url
from test_s6_workloads import REPEATS
from test_scenario_api import client, login, scenario_payload
from test_student_failures import (
    test_disconnect_cancels_sdk_read_and_preserves_teacher as disconnect,
)
from test_student_failures import (
    test_final_commit_failure_never_emits_completed as storage_failure,
)
from test_student_generation import frames, student

from src.models import ApiUsageLog, AppSetting, ComparisonRun
from src.services.call_admission import active_calls, registered_calls

__all__ = ["client", "comparison", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize("repeat", REPEATS)
async def test_explicit_capacity_slow_mentor_leaves_student_and_next_input_free(
    data, client, mentor, repeat
):
    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = {
        **setting.limits_json,
        "total": 24,
        "openai": 24,
        "admin": 3,
    }
    await data.db.commit()
    observation = Observation(
        "slow_mentor_student",
        repeat,
        {"fixture": "two_student_turns"},
        limits=setting.limits_json,
    )
    coaching = Observation(
        "slow_mentor_final",
        repeat,
        {"fixture": "first_target_turn"},
        limits=setting.limits_json,
    )
    coaching.conditions.update(mock_body_delay_ms=None, mock_barrier=True)
    entered, release = asyncio.Event(), asyncio.Event()
    source = copy.deepcopy(mentor.stream.events)

    async def create(**kwargs):
        if kwargs.get("stream"):
            return observation.stream(source, options=kwargs)
        call = dict(
            start=monotonic_ns(),
            first_body=None,
            end=None,
            **input_metadata(kwargs),
            model_options={
                key: kwargs[key]
                for key in ("model", "max_output_tokens", "reasoning")
            },
        )
        coaching.conditions["model"] = kwargs["model"]
        coaching.calls.append(call)
        entered.set()
        await release.wait()
        call["end"] = monotonic_ns()
        return SimpleNamespace(
            output_text=mentor_output("Late coaching"), usage=None
        )

    mentor.responses.create.side_effect = create
    login(client, data.owner)
    start = monotonic_ns()
    first = await complete_turn(client, data)
    samples = [(start, monotonic_ns(), first)]
    path = mentor_url(data, first)
    payload = dict(request_id=str(uuid4()), trigger="manual")
    mentor_start = monotonic_ns()
    pending = asyncio.create_task(client.post(path, json=payload))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert (await client.post(path, json=payload)).json()[
            "status"
        ] == "running"
        start = monotonic_ns()
        second = await asyncio.wait_for(
            complete_turn(client, data, "Next input"), 10
        )
        samples.append((start, monotonic_ns(), second))
        assert second["turn_index"] == 2
        state = (await client.get(f"/runs/{second['run_id']}")).json()
        assert state["status"] == "completed"
        assert state["message"]["content"] == "Student answer"
        updates = await client.get(
            f"/sessions/{data.session.id}/messages/updates"
        )
        assert updates.text.count("data-message-id=") == 4
        assert not pending.done() and len(coaching.calls) == 1
        assert len(active_calls) == 1
    finally:
        release.set()
        response = await asyncio.wait_for(pending, 10)
    assert frames(response)[-1][0] == "output.completed"
    assert frames(response)[-1][1]["turn_id"] == first["turn_id"]
    mentor_end = monotonic_ns()
    assert (await client.post(path, json=payload)).json()[
        "status"
    ] == "completed"
    assert mentor.responses.create.await_count == 3
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
    assert len(ledger) == 3
    assert all(
        entry.status == "completed" and entry.attempt_no == 1
        for entry in ledger
    )
    assert sorted(entry.role for entry in ledger) == [
        "mentor",
        "student",
        "student",
    ]
    for index, (start, end, turn) in enumerate(samples):
        row = observation.record(
            "completed",
            start,
            end,
            ledger=[
                entry for entry in ledger if entry.run_id == turn["run_id"]
            ],
            calls=observation.calls[index : index + 1],
        )
        row["conditions"]["completed_while_mentor_pending"] = turn == second
        write_sample(row)
    write_sample(
        coaching.record(
            "completed",
            mentor_start,
            mentor_end,
            ledger=[entry for entry in ledger if entry.role == "mentor"],
            final=True,
        )
    )
    assert not active_calls and not registered_calls


@pytest.mark.parametrize("repeat", REPEATS)
@pytest.mark.parametrize("failure", ["provider", "429"])
async def test_panel_fault_samples_reuse_sibling_and_no_retry_contract(
    data, client, student, comparison, failure, repeat
):
    observation = Observation(
        f"panel_{failure}",
        repeat,
        {"fixture": "three_panels", "fault": failure},
        limits=(await data.db.get(AppSetting, 1)).limits_json,
    )
    observation.conditions["mock_body_delay_ms"] = None
    start = monotonic_ns()
    await panel_failure(data, client, student, comparison, failure)
    end = monotonic_ns()
    async with data.factory() as db:
        record = (await db.scalars(select(ComparisonRun))).one()
        ledger = (await db.scalars(select(ApiUsageLog))).all()
    state = (await client.get(f"/admin/comparisons/{record.id}")).json()
    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).json() == state
    async with data.factory() as db:
        assert {
            entry.id for entry in (await db.scalars(select(ApiUsageLog))).all()
        } == {entry.id for entry in ledger}
    assert student.responses.create.await_count == len(ledger) == 3
    assert all(entry.attempt_no == 1 for entry in ledger)
    for panel in state["panels"]:
        row = observation.record(
            panel["status"],
            start,
            end,
            calls=[],
            ledger=[
                entry for entry in ledger if entry.run_id == panel["run_id"]
            ],
        )
        row["conditions"].update(
            panel_id=panel["panel_id"],
            attempt_unit="role_or_panel",
            error_code=panel["error_code"],
        )
        options = next(
            call.kwargs
            for call in student.responses.create.call_args_list
            if call.kwargs["max_output_tokens"] == 1499 + int(panel["panel_id"])
        )
        row["conditions"].update(
            **input_metadata(options),
            model_options={
                key: options[key]
                for key in ("model", "max_output_tokens", "reasoning")
            },
        )
        row["durations_ms"]["comparison_http_upper_bound"] = row[
            "durations_ms"
        ]["request"]
        row["durations_ms"]["request"] = None
        row[
            "unknown_reason"
        ] += "; parent fault check duration includes reader/replay; per-panel time unobserved"
        write_sample(row)


@pytest.mark.parametrize("repeat", REPEATS)
@pytest.mark.parametrize("fault", ["storage_failure", "disconnect"])
async def test_student_fault_samples_reuse_persistence_contract(
    data, client, student, monkeypatch, fault, repeat
):
    observation = Observation(
        f"student_{fault}",
        repeat,
        {"fixture": "student_fault", "fault": fault},
        limits=(await data.db.get(AppSetting, 1)).limits_json,
    )
    observation.conditions["mock_body_delay_ms"] = None
    start = monotonic_ns()
    if fault == "storage_failure":
        await storage_failure(data, client, student)
    else:
        await disconnect(
            data, client, student, monkeypatch, False, "delta", None
        )
    end = monotonic_ns()
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
    assert len(ledger) == student.responses.create.await_count == 1
    state = (await client.get(f"/runs/{ledger[0].run_id}")).json()
    replay = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json=dict(
            request_id=state["request_id"],
            content=(
                "Keep this teacher"
                if fault == "storage_failure"
                else "Keep after disconnect"
            ),
        ),
    )
    assert replay.json() == state
    assert student.responses.create.await_count == 1
    async with data.factory() as db:
        assert (await db.scalars(select(ApiUsageLog))).one().id == ledger[0].id
    row = observation.record(
        "failed" if fault == "storage_failure" else "cancelled",
        start,
        end,
        ledger=ledger,
        calls=[],
    )
    row["conditions"]["terminal_status"] = state["status"]
    row["conditions"]["error_code"] = state["error_code"]
    options = student.responses.create.call_args.kwargs
    row["conditions"].update(
        **input_metadata(options),
        model_options={
            key: options[key]
            for key in ("model", "max_output_tokens", "reasoning")
        },
    )
    row[
        "unknown_reason"
    ] += "; reused fault check includes reader assertions; SDK duration unobserved"
    write_sample(row)
    assert not active_calls and not registered_calls

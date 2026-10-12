"""Synthetic measurements at existing HTTP/SSE, SQLite and SDK seams."""

import asyncio
import copy
import json
import os
from time import monotonic_ns
from types import SimpleNamespace
from uuid import uuid4

import httpx
import httpx2
import pytest
from s6_measurement import write_sample
from s6_observer import Observation, input_metadata
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport, result_for
from test_analysis_runs import terminal
from test_comparison_concurrency import add_panels
from test_comparison_student import comparison
from test_mentor_generation import mentor
from test_native_analysis import native_analysis
from test_scenario_api import client, login, scenario_payload
from test_student_generation import frames, student
from test_student_probe import response_body

from src.main import app
from src.models import ApiUsageLog, AppSetting, Session, User
from src.services.call_admission import active_calls, registered_calls

__all__ = ["client", "comparison", "mentor", "scenario_payload", "student"]
REPEATS = range(int(os.environ.get("S6_MEASUREMENT_REPEATS", "1")))


@pytest.mark.parametrize("repeat", REPEATS)
async def test_student_measurement_and_zero_call_replay(
    data, client, student, repeat
):
    observation = Observation("student", repeat, {"content": "Why?"})
    observation.conditions["limits"] = (
        await data.db.get(AppSetting, 1)
    ).limits_json
    source = copy.deepcopy(student.stream.events)
    student.responses.create.side_effect = lambda **kwargs: observation.stream(
        source, options=kwargs
    )
    login(client, data.owner)
    payload = dict(request_id=str(uuid4()), content="Why?")
    path = f"/sessions/{data.session.id}/turns/stream"
    start = monotonic_ns()
    response = await client.post(path, json=payload)
    finish = monotonic_ns()
    assert frames(response)[-1][0] == "output.completed"
    replay = await client.post(path, json=payload)
    assert replay.json()["status"] == "completed"
    assert student.responses.create.await_count == 1
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
        assert len(ledger) == 1 and ledger[0].role == "student"
        observation.record("completed", start, finish, ledger=ledger)
    row = observation.rows[0]
    assert row["durations_ms"]["provider_first_body"] is not None
    assert row["durations_ms"]["first_visible_body"] is None
    assert row["ledger"][0]["attempt_no"] == 1
    assert "Why?" not in json.dumps(row)
    assert not active_calls and not registered_calls
    for row in observation.rows:
        write_sample(row)


@pytest.mark.parametrize("repeat", REPEATS)
@pytest.mark.parametrize("capacity", ["default", "24"])
@pytest.mark.parametrize("order", ["classroom_first", "comparison_first"])
async def test_twenty_teachers_and_three_panel_capacity(
    data, client, student, comparison, capacity, order, repeat
):
    setting = await data.db.get(AppSetting, 1)
    if capacity == "24":
        setting.limits_json = {
            **setting.limits_json,
            "total": 24,
            "openai": 24,
            "admin": 3,
        }
    limits = copy.deepcopy(setting.limits_json)
    add_panels(comparison, 3)
    for index, panel in enumerate(comparison["panels"]):
        panel["model_config"]["options"]["max_output_tokens"] = 1600 + index
    teachers = []
    for index in range(20):
        owner = User(
            username=f"measurement_{index}",
            nickname="Synthetic",
            group_id=data.owner.group_id,
        )
        data.db.add(owner)
        await data.db.flush()
        session = Session(
            teacher_id=owner.id,
            scenario_id=data.scenario.id,
            config_snapshot_json=copy.deepcopy(
                data.session.config_snapshot_json
            ),
            config_hash=data.session.config_hash,
            snapshot_origin="native",
            source_scenario_version=1,
            snapshot_created_at=data.session.snapshot_created_at,
        )
        data.db.add(session)
        await data.db.flush()
        teachers.append((owner, session.id))
    await data.db.commit()
    observation = Observation(
        f"capacity_{capacity}_{order}",
        repeat,
        {
            key: value
            for key, value in comparison.items()
            if key != "request_id"
        },
        limits=limits,
    )
    gate = asyncio.Event()
    source = copy.deepcopy(student.stream.events)

    def create(**kwargs):
        stream = observation.stream(source, barrier=gate, options=kwargs)
        observation.calls[-1]["panel_id"] = (
            str(kwargs["max_output_tokens"] - 1599)
            if kwargs["max_output_tokens"] >= 1600
            else None
        )
        return stream

    student.responses.create.side_effect = create
    tasks, clients = [], []
    starts = []

    async def send(owner, path, payload, panels):
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        clients.append(http)
        login(http, owner)
        start = monotonic_ns()
        response = await http.post(path, json=payload)
        end = monotonic_ns()
        status = "refused" if response.status_code == 429 else "completed"
        assert response.status_code in {200, 429}, response.text
        events = frames(response) if response.status_code == 200 else []
        if events:
            assert (
                sum(kind == "output.completed" for kind, _ in events) == panels
            )
        ids = {
            value["run_id"] for kind, value in events if kind == "run.accepted"
        }
        async with data.factory() as db:
            ledger = (
                (
                    await db.scalars(
                        select(ApiUsageLog).where(ApiUsageLog.run_id.in_(ids))
                    )
                ).all()
                if ids
                else []
            )
        assert len(ledger) == (panels if status == "completed" else 0)
        for _ in range(panels):
            row = observation.record(
                status, start, end, ledger=ledger, calls=[]
            )
            row["conditions"]["attempt_unit"] = "role_or_panel"
            if panels == 3:
                row["durations_ms"]["comparison_http_upper_bound"] = row[
                    "durations_ms"
                ]["request"]
                row["durations_ms"]["request"] = None
                row[
                    "unknown_reason"
                ] += "; per-panel completion unavailable in buffered response; parent HTTP duration repeated as an upper bound for three attempts"
        starts.append((path.startswith("/admin"), start, end))

    async def wait_settled():
        # Every launched request is either inside the held SDK mock or refused.
        async with asyncio.timeout(30):
            while sum(
                (3 if kind == "comparison" else 1)
                for task, kind in zip(tasks, launched)
                if task.done()
            ) + len(observation.calls) < sum(
                3 if i == "comparison" else 1 for i in launched
            ):
                await asyncio.sleep(0.01)

    launched = []

    async def launch_classroom():
        for owner, sid in teachers:
            tasks.append(
                asyncio.create_task(
                    send(
                        owner,
                        f"/sessions/{sid}/turns/stream",
                        dict(
                            request_id=str(uuid4()),
                            content="Synthetic question",
                        ),
                        1,
                    )
                )
            )
            launched.append("student")
        await wait_settled()

    async def launch_comparison():
        tasks.append(
            asyncio.create_task(
                send(data.admin, "/admin/comparisons", comparison, 3)
            )
        )
        launched.append("comparison")
        await wait_settled()

    try:
        if order == "classroom_first":
            await launch_classroom()
            await launch_comparison()
        else:
            await launch_comparison()
            await launch_classroom()
        overlap = len(active_calls)
        if capacity == "24":
            assert overlap == 23 and len(observation.calls) == 23
        else:
            assert overlap <= limits["total"]
        gate.set()
        await asyncio.gather(*tasks)
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(http.aclose() for http in clients))
    assert len(observation.rows) == 23
    assert not active_calls and not registered_calls
    comparison_start = next(
        (start for admin, start, _ in starts if admin), None
    )
    for row in observation.rows:
        row["conditions"]["barrier_overlap"] = overlap
        row["raw_ns"]["provider_calls"] = observation.calls
        panel_starts = [
            call["start"]
            for call in observation.calls
            if call["panel_id"] is not None
        ]
        row["durations_ms"]["comparison_start_skew"] = (
            (max(panel_starts) - min(panel_starts)) / 1_000_000
            if panel_starts
            else None
        )
        row["raw_ns"]["comparison_request_start"] = comparison_start
        write_sample(row)


@pytest.mark.parametrize("repeat", REPEATS)
async def test_mentor_final_result_measurement(data, client, mentor, repeat):
    from lesson_fixtures import mentor_output
    from test_mentor_generation import complete_turn, mentor_url

    login(client, data.owner)
    turn = await complete_turn(client, data)
    observation = Observation(
        "mentor_final", repeat, {"target_turn": "synthetic_completed_pair"}
    )
    observation.conditions["mock_body_delay_ms"] = 30
    observation.conditions["limits"] = (
        await data.db.get(AppSetting, 1)
    ).limits_json

    async def create(**kwargs):
        call = dict(
            **input_metadata(kwargs),
            start=monotonic_ns(),
            first_body=None,
            end=None,
            model_options={
                key: kwargs[key]
                for key in ("model", "max_output_tokens", "reasoning")
            },
        )
        observation.calls.append(call)
        await asyncio.sleep(0.03)
        call["end"] = monotonic_ns()
        return SimpleNamespace(
            output_text=mentor_output("Synthetic coaching"), usage=None
        )

    mentor.responses.create.side_effect = create
    start = monotonic_ns()
    response = await client.post(
        mentor_url(data, turn),
        json=dict(request_id=str(uuid4()), trigger="manual"),
    )
    end = monotonic_ns()
    assert frames(response)[-1][0] == "output.completed"
    async with data.factory() as db:
        ledger = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.role == "mentor")
            )
        ).all()
    assert len(ledger) == 1
    write_sample(
        observation.record("completed", start, end, ledger=ledger, final=True)
    )


@pytest.mark.parametrize("repeat", REPEATS)
async def test_short_analysis_one_call_and_replay_measurement(
    data, client, monkeypatch, repeat
):
    await native_analysis(data, monkeypatch)
    login(client, data.owner)
    observation = Observation(
        "analysis_final",
        repeat,
        {"fixture": "native_analysis", "pairs": 1, "unanswered_teacher": 1},
    )
    observation.conditions["limits"] = (
        await data.db.get(AppSetting, 1)
    ).limits_json

    async def upstream(request, body):
        call = dict(
            **input_metadata(body),
            start=monotonic_ns(),
            first_body=None,
            end=None,
            model_options={
                key: body[key]
                for key in ("model", "max_output_tokens", "reasoning")
            },
        )
        observation.calls.append(call)
        await asyncio.sleep(0.01)
        value = httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )
        call["end"] = monotonic_ns()
        return value

    clients, calls = analysis_transport(monkeypatch, upstream)
    payload = dict(request_id=str(uuid4()))
    path = f"/sessions/{data.session.id}/analyze"
    start = monotonic_ns()
    response = await client.post(path, json=payload)
    assert response.status_code == 202, response.text
    state = await terminal(client, response.json()["actions"]["status"])
    end = monotonic_ns()
    assert state["latest_run"]["status"] == "ok"
    assert (await client.post(path, json=payload)).status_code == 200
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
    assert len(ledger) == 1
    row = observation.record("completed", start, end, ledger=ledger, final=True)
    row[
        "unknown_reason"
    ] += "; analysis final includes 20ms test polling overhead"
    write_sample(row)


@pytest.mark.parametrize("repeat", REPEATS)
async def test_non_llm_admin_read_and_save_measurement(
    data, client, student, repeat
):
    login(client, data.admin)
    observation = Observation(
        "admin_read", repeat, {"fixture": "admin_ai_settings"}
    )
    observation.conditions["limits"] = (
        await data.db.get(AppSetting, 1)
    ).limits_json
    start = monotonic_ns()
    response = await client.get("/admin/ai/state")
    end = monotonic_ns()
    assert response.status_code == 200
    setting = response.json()["settings"]
    write_sample(observation.record("completed", start, end, calls=[]))
    observation = Observation(
        "admin_save", repeat, {"fixture": "admin_ai_settings"}
    )
    observation.conditions["limits"] = setting["limits"]
    payload = dict(
        expected_version=setting["settings_version"],
        defaults={role: None for role in ("student", "mentor", "analysis")},
        limits=setting["limits"],
        timeouts=setting["timeouts"],
    )
    start = monotonic_ns()
    response = await client.post("/admin/ai/settings/update", json=payload)
    end = monotonic_ns()
    assert response.status_code == 200, response.text
    assert student.responses.create.await_count == 0
    write_sample(observation.record("completed", start, end, calls=[]))


@pytest.mark.parametrize("repeat", REPEATS)
@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
async def test_failure_and_cancellation_keep_unknown_durations(
    data, client, student, comparison, repeat, outcome
):
    from test_comparison_streaming import live_comparison, next_event

    login(client, data.admin)
    observation = Observation(
        f"comparison_{outcome}",
        repeat,
        {"fixture": "single_comparison", "outcome": outcome},
    )
    observation.conditions["limits"] = (
        await data.db.get(AppSetting, 1)
    ).limits_json
    gate = asyncio.Event() if outcome == "cancelled" else None
    source = (
        [RuntimeError("synthetic failure")]
        if outcome == "failed"
        else copy.deepcopy(student.stream.events)
    )
    student.responses.create.side_effect = lambda **kwargs: observation.stream(
        source, barrier=gate, options=kwargs
    )
    start = monotonic_ns()
    async with live_comparison(client, comparison) as live:
        accepted = await next_event(live, "run.accepted", "1")
        if outcome == "cancelled":
            async with asyncio.timeout(10):
                while not observation.calls:
                    await asyncio.sleep(0.01)
            response = await client.post(
                f"/admin/comparisons/{accepted['comparison_run_id']}/panels/1/stop",
                json={"generation_id": accepted["run_id"]},
            )
            assert response.status_code == 200
        await next_event(live, f"run.{outcome}", "1")
    end = monotonic_ns()
    async with data.factory() as db:
        ledger = (await db.scalars(select(ApiUsageLog))).all()
    assert len(ledger) == 1 and ledger[0].status == outcome
    row = observation.record(outcome, start, end, ledger=ledger)
    assert row["durations_ms"]["provider_first_body"] is None
    assert row["durations_ms"]["provider_end"] is None
    assert not active_calls and not registered_calls
    write_sample(row)

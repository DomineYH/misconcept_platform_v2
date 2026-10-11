"""Panel retries through authenticated HTTP/SSE and mocked provider transport."""

import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_comparison_analysis import analysis_comparison
from test_comparison_concurrency import add_panels, panel_streams
from test_comparison_mentor import mentor_comparison
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import FakeStream, frames

from src.models import AppSetting, ComparisonRun, GenerationRun
from src.services.call_admission import active_calls, registered_calls
from src.services.comparison_state import utcnow

__all__ = [
    "client",
    "comparison",
    "scenario_payload",
    "student",
    "mentor_comparison",
    "analysis_comparison",
]


async def failed_comparison(data, client, student, comparison):
    login(client, data.admin)
    add_panels(comparison, 2)
    streams = panel_streams(student, comparison)
    completed = copy.deepcopy(streams[1].events)
    streams[1].events = [streams[1].events[0], RuntimeError("private failure")]
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    state = (await client.get(path)).json()
    assert [panel["status"] for panel in state["panels"]] == [
        "completed",
        "failed",
    ]
    student.responses.create.side_effect = lambda **kwargs: FakeStream(
        copy.deepcopy(completed)
    )
    return path, state


async def test_retry_only_failed_latest_panel_preserves_conditions_and_attempts(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    old = before["panels"][1]
    body = dict(request_id=str(uuid4()), generation_id=old["run_id"])
    response = await client.post(f"{path}/panels/2/retry", json=body)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == [
        "run.accepted",
        "output.delta",
        "output.delta",
        "output.completed",
    ]
    assert all(value["panel_id"] == "2" for _, value in events)
    assert events[0][1]["request_id"] == body["request_id"]
    after = (await client.get(path)).json()
    assert after["status"] == "finished"
    assert after["panels"][0] == before["panels"][0]
    latest = after["panels"][1]
    assert latest["status"] == "completed" and latest["run_id"] != old["run_id"]
    assert latest["turn_id"] == old["turn_id"]
    assert latest["attempts"] == [old]
    for key in (
        "source_snapshot",
        "context_hash",
        "canonical_context",
        "current_message",
        "created_at",
        "expires_at",
    ):
        assert after[key] == before[key]
    assert student.responses.create.await_count == 3
    calls = student.responses.create.call_args_list
    assert calls[1].kwargs == calls[2].kwargs
    assert (
        await client.post(f"{path}/panels/2/retry", json=body)
    ).json() == after
    assert (
        await client.post(
            f"{path}/panels/2/retry",
            json={**body, "generation_id": latest["run_id"]},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"{path}/panels/2/retry", json={**body, "request_id": str(uuid4())}
        )
    ).status_code == 409
    assert (
        await client.post(
            f"{path}/panels/1/retry",
            json=dict(
                request_id=str(uuid4()),
                generation_id=before["panels"][0]["run_id"],
            ),
        )
    ).status_code == 409
    assert student.responses.create.await_count == 3
    assert not active_calls and not registered_calls


@pytest.mark.parametrize(
    "failure",
    [
        "connection",
        "verification",
        "prompt",
        "slots",
        "expiry",
        "owner",
        "permission",
        "storage",
    ],
)
async def test_retry_admission_failure_preserves_previous_results_and_calls(
    data, client, student, comparison, failure
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import SQLAlchemyError

    path, before = await failed_comparison(data, client, student, comparison)
    comparison_id = before["comparison_run_id"]
    occupying = None
    gate = asyncio.Event()
    if failure == "connection":
        student.connection.enabled = False
    elif failure == "verification":
        student.model.verification_state = {}
    elif failure == "permission":
        data.admin.role = "teacher"
    elif failure == "slots":
        setting = await data.db.get(AppSetting, 1)
        setting.limits_json = {**setting.limits_json, "admin": 1}
    elif failure in ("expiry", "prompt"):
        record = await data.db.get(ComparisonRun, comparison_id)
        if failure == "expiry":
            record.expires_at = utcnow() - timedelta(seconds=1)
        else:
            panels = copy.deepcopy(record.panel_configs)
            panels[1]["prompt_version"] = "old-prompt"
            record.panel_configs = panels
    await data.db.commit()
    if failure == "owner":
        data.other.role = "admin"
        await data.db.commit()
        login(client, data.other)
    if failure == "slots":
        data.other.role = "admin"
        await data.db.commit()
        stream = student.responses.create.side_effect(max_output_tokens=1501)
        stream.events.insert(0, gate)
        student.responses.create.side_effect = None
        student.responses.create.return_value = stream
        login(client, data.other)
        occupying = asyncio.create_task(
            client.post(
                "/admin/comparisons",
                json={
                    **comparison,
                    "request_id": str(uuid4()),
                    "panels": comparison["panels"][:1],
                },
            )
        )
        await asyncio.wait_for(stream.read_started.wait(), 10)
        login(client, data.admin)

    def reject_commit(conn):
        raise SQLAlchemyError("private commit failure")

    if failure == "storage":
        sql_event.listen(data.engine.sync_engine, "commit", reject_commit)
    try:
        result = await client.post(
            f"{path}/panels/2/retry",
            json=dict(
                request_id=str(uuid4()),
                generation_id=before["panels"][1]["run_id"],
            ),
        )
    finally:
        if failure == "storage":
            sql_event.remove(data.engine.sync_engine, "commit", reject_commit)
        gate.set()
        if occupying is not None:
            assert (await occupying).status_code == 200
            login(client, data.admin)
    expected = {
        "connection": 503,
        "verification": 503,
        "prompt": 409,
        "slots": 429,
        "expiry": 404,
        "owner": 404,
        "permission": 403,
        "storage": 503,
    }
    assert result.status_code == expected[failure], result.text
    assert "private commit failure" not in result.text
    if failure == "slots":
        assert result.headers["Retry-After"] == "1"
    async with data.factory() as db:
        record = await db.get(ComparisonRun, comparison_id)
        assert record.status == "finished"
        runs = (
            await db.scalars(
                select(GenerationRun).where(
                    GenerationRun.comparison_run_id == comparison_id
                )
            )
        ).all()
        assert len(runs) == 2
        assert {run.status for run in runs} == {"completed", "failed"}
        assert (
            next(run for run in runs if run.panel_id == "1").outcome_json
            is not None
        )
    assert student.responses.create.await_count == (
        3 if failure == "slots" else 2
    )
    assert not active_calls and not registered_calls
    if failure not in ("expiry", "owner", "permission", "prompt"):
        assert (await client.get(path)).json() == before


async def test_retry_running_replay_and_stop_reject_old_generation(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    old = before["panels"][1]
    stream = student.responses.create.side_effect(max_output_tokens=1501)
    gate = asyncio.Event()
    stream.events.insert(1, gate)
    student.responses.create.side_effect = None
    student.responses.create.return_value = stream
    body = dict(request_id=str(uuid4()), generation_id=old["run_id"])
    first = asyncio.create_task(
        client.post(f"{path}/panels/2/retry", json=body)
    )
    try:
        await asyncio.wait_for(stream.read_started.wait(), 10)
        state = (await client.get(path)).json()
        assert state["status"] == "running"
        current = state["panels"][1]
        assert current["run_id"] != old["run_id"] and current["attempts"] == [
            old
        ]
        assert (
            await client.post(f"{path}/panels/2/retry", json=body)
        ).json() == state
        assert (
            await client.post(
                f"{path}/panels/2/retry",
                json={**body, "request_id": str(uuid4())},
            )
        ).status_code == 409
        assert (
            await client.post(
                "/admin/comparisons",
                json={**comparison, "request_id": str(uuid4())},
            )
        ).status_code == 409
        assert (
            await client.post(
                f"{path}/panels/2/stop", json={"generation_id": old["run_id"]}
            )
        ).status_code == 409
        assert (
            await client.post(
                f"{path}/stop",
                json={
                    "generations": [
                        {"panel_id": "2", "generation_id": old["run_id"]}
                    ]
                },
            )
        ).status_code == 409
        assert (
            await client.post(
                f"{path}/panels/2/stop",
                json={"generation_id": current["run_id"]},
            )
        ).status_code == 200
    finally:
        gate.set()
        result = await first
    assert frames(result)[-1][0] == "run.cancelled"
    state = (await client.get(path)).json()
    assert (
        state["status"] == "finished"
        and state["panels"][1]["status"] == "cancelled"
    )
    assert state["panels"][0] == before["panels"][0]
    assert student.responses.create.await_count == 3
    assert stream.closed and not active_calls and not registered_calls


async def test_retry_uses_frozen_options_after_defaults_change(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    student.model.default_options_json = {"max_output_tokens": 2000}
    await data.db.commit()
    result = await client.post(
        f"{path}/panels/2/retry",
        json=dict(
            request_id=str(uuid4()), generation_id=before["panels"][1]["run_id"]
        ),
    )
    assert (
        result.status_code == 200
        and frames(result)[-1][0] == "output.completed"
    )
    assert (
        student.responses.create.call_args.kwargs["max_output_tokens"] == 1501
    )


@pytest.fixture
def race_comparison(request):
    return request.getfixturevalue(request.param)


@pytest.mark.parametrize(
    "race_comparison",
    ["comparison", "mentor_comparison", "analysis_comparison"],
    indirect=True,
)
async def test_late_cancelled_retry_stream_never_emits_new_attempt_result(
    data, client, student, race_comparison
):
    from test_comparison_streaming import live_comparison, next_event

    comparison = race_comparison
    if comparison["target_role"] == "student":
        path, before = await failed_comparison(
            data, client, student, comparison
        )
    else:
        import json
        from types import SimpleNamespace

        from lesson_fixtures import mentor_output
        from s4_analysis_fixtures import analysis_reply, prompt_inputs

        login(client, data.admin)
        add_panels(comparison, 2)
        cap = comparison["panels"][0]["model_config"]["options"][
            "max_output_tokens"
        ]
        comparison["panels"][1]["model_config"]["options"][
            "max_output_tokens"
        ] = (cap + 1)
        failing = True

        async def create(**kwargs):
            result = (
                mentor_output("Mentor coaching")
                if comparison["target_role"] == "mentor"
                else json.dumps(
                    analysis_reply(prompt_inputs(kwargs)["messages"])
                )
            )
            return SimpleNamespace(
                output_text=(
                    "INVALID_JSON"
                    if failing and kwargs["max_output_tokens"] == cap + 1
                    else result
                ),
                usage=None,
            )

        student.responses.create.side_effect = create
        initial = await client.post("/admin/comparisons", json=comparison)
        path = (
            f"/admin/comparisons/{frames(initial)[0][1]['comparison_run_id']}"
        )
        before = (await client.get(path)).json()
        assert before["panels"][1]["status"] == "failed"
        failing = False
    headers, release = asyncio.Event(), asyncio.Event()
    login(client, data.admin)

    async def hold_headers():
        headers.set()
        await release.wait()

    body = dict(
        request_id=str(uuid4()), generation_id=before["panels"][1]["run_id"]
    )
    async with live_comparison(
        client, body, before_body=hold_headers, path=f"{path}/panels/2/retry"
    ) as old_stream:
        try:
            await asyncio.wait_for(headers.wait(), 10)
            running = (await client.get(path)).json()["panels"][1]
            stopped = await client.post(
                f"{path}/panels/2/stop",
                json={"generation_id": running["run_id"]},
            )
            assert stopped.status_code == 200
            retry = await client.post(
                f"{path}/panels/2/retry",
                json=dict(
                    request_id=str(uuid4()), generation_id=running["run_id"]
                ),
            )
            assert (
                retry.status_code == 200
                and frames(retry)[-1][0] == "output.completed"
            )
            latest = (await client.get(path)).json()
        finally:
            release.set()
        terminal = await next_event(old_stream, "run.cancelled", "2")
        await asyncio.wait_for(old_stream.task, 10)
        assert terminal["run_id"] == running["run_id"]
        assert terminal["status"] == "cancelled"
        assert terminal["text"] != "Student answer"
    assert (await client.get(path)).json() == latest
    assert (
        await client.post(
            f"{path}/panels/2/stop", json={"generation_id": running["run_id"]}
        )
    ).status_code == 409
    assert student.responses.create.await_count == 3
    assert not active_calls and not registered_calls


async def test_restart_interrupted_panel_can_be_retried_without_restarting_sibling(
    data, client, student, comparison
):
    from src.services.generation_lifecycle import interrupt_orphans

    path, before = await failed_comparison(data, client, student, comparison)
    async with data.factory() as db:
        record = await db.get(ComparisonRun, before["comparison_run_id"])
        run = await db.get(GenerationRun, before["panels"][1]["run_id"])
        record.status = run.status = "running"
        run.finished_at = None
        await db.commit()
    await interrupt_orphans(data.factory)
    recovered = (await client.get(path)).json()
    assert (
        recovered["status"] == "finished"
        and recovered["panels"][1]["status"] == "interrupted"
    )
    result = await client.post(
        f"{path}/panels/2/retry",
        json=dict(
            request_id=str(uuid4()),
            generation_id=recovered["panels"][1]["run_id"],
        ),
    )
    assert frames(result)[-1][0] == "output.completed"
    final = (await client.get(path)).json()
    assert final["panels"][0] == before["panels"][0]
    assert final["panels"][1]["attempts"] == [recovered["panels"][1]]
    assert student.responses.create.await_count == 3


@pytest.mark.parametrize(
    "role,status,fixture",
    [
        ("mentor", "completed", "mentor_coaching"),
        ("mentor", "completed", "mentor_no_intervention"),
        ("analysis", "ok", "analysis_ok"),
        ("analysis", "degraded", "analysis_degraded"),
    ],
)
async def test_recovery_preserves_structured_terminal_contract(
    data, client, student, comparison, role, status, fixture
):
    import json

    from s5_terminal_fixtures import terminal_fixtures

    path, before = await failed_comparison(data, client, student, comparison)
    result = terminal_fixtures()[fixture]
    async with data.factory() as db:
        record = await db.get(ComparisonRun, before["comparison_run_id"])
        record.target_role = role
        for panel in before["panels"]:
            run = await db.get(GenerationRun, panel["run_id"])
            run.operation, run.status = role, status
            run.outcome_json = json.dumps(
                dict(
                    result=result,
                    raw_error="PRIVATE_TERMINAL_METADATA",
                    reasoning="PRIVATE_TERMINAL_METADATA",
                )
            )
        await db.commit()
    recovered = (await client.get(path)).json()
    assert recovered["panels"][0]["result"] == result
    assert recovered["panels"][0]["status"] == status
    assert "PRIVATE_TERMINAL_METADATA" not in json.dumps(recovered)
    assert student.responses.create.await_count == 2


async def test_simultaneous_retry_accepts_one_stream_and_preserves_success_panel(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    stream = student.responses.create.side_effect(max_output_tokens=1501)
    gate = asyncio.Event()
    stream.events.insert(0, gate)
    student.responses.create.side_effect = None
    student.responses.create.return_value = stream
    tasks = [
        asyncio.create_task(
            client.post(
                f"{path}/panels/2/retry",
                json=dict(
                    request_id=str(uuid4()),
                    generation_id=before["panels"][1]["run_id"],
                ),
            )
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(stream.read_started.wait(), 10)
        done, _ = await asyncio.wait(
            tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED
        )
        assert len(done) == 1 and next(iter(done)).result().status_code == 409
        current = (await client.get(path)).json()
        assert (
            current["status"] == "running"
            and current["panels"][1]["status"] == "running"
        )
        assert current["panels"][0] == before["panels"][0]
    finally:
        gate.set()
        responses = await asyncio.gather(*tasks)
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert student.responses.create.await_count == 3
    assert not active_calls and not registered_calls


async def test_retry_conflicts_with_another_active_comparison_for_same_admin(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    stream = student.responses.create.side_effect(max_output_tokens=1501)
    gate = asyncio.Event()
    stream.events.insert(0, gate)
    student.responses.create.side_effect = None
    student.responses.create.return_value = stream
    task = asyncio.create_task(
        client.post(
            "/admin/comparisons",
            json={
                **comparison,
                "request_id": str(uuid4()),
                "panels": comparison["panels"][:1],
            },
        )
    )
    try:
        await asyncio.wait_for(stream.read_started.wait(), 10)
        result = await client.post(
            f"{path}/panels/2/retry",
            json=dict(
                request_id=str(uuid4()),
                generation_id=before["panels"][1]["run_id"],
            ),
        )
        assert (
            result.status_code == 409
            and result.json()["detail"]["code"] == "comparison_busy"
        )
        assert (await client.get(path)).json() == before
    finally:
        gate.set()
        assert (await task).status_code == 200
    assert student.responses.create.await_count == 3
    assert not active_calls and not registered_calls


async def test_recovery_missing_panel_attempt_returns_storage_error_without_calls(
    data, client, student, comparison
):
    path, before = await failed_comparison(data, client, student, comparison)
    async with data.factory() as db:
        record = await db.get(ComparisonRun, before["comparison_run_id"])
        record.panel_configs = [
            *record.panel_configs,
            {**record.panel_configs[0], "panel_id": "missing"},
        ]
        await db.commit()
    response = await client.get(path)
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "storage_error"
    )
    assert student.responses.create.await_count == 2

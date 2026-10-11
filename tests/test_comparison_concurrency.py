"""Concurrent comparisons at HTTP/SSE, SQLite and provider transport boundaries."""

import asyncio
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import FakeStream, frames

from src.models import ApiUsageLog, AppSetting, ComparisonRun, GenerationRun
from src.services.call_admission import active_calls, registered_calls

__all__ = ["client", "comparison", "scenario_payload", "student"]


def add_panels(comparison, count):
    comparison["panels"] = [
        {**copy.deepcopy(comparison["panels"][0]), "panel_id": str(index + 1)}
        for index in range(count)
    ]


def panel_streams(student, comparison):
    streams = []
    for index, panel in enumerate(comparison["panels"]):
        panel["model_config"]["options"]["max_output_tokens"] = 1500 + index
        streams.append(FakeStream(copy.deepcopy(student.stream.events)))
    student.responses.create.side_effect = lambda **kwargs: streams[
        kwargs["max_output_tokens"] - 1500
    ]
    return streams


@pytest.mark.parametrize("count", [2, 3])
@pytest.mark.parametrize("limit", ["total", "openai", "admin"])
async def test_all_panel_admission_is_atomic_without_calls_or_records(
    data, client, student, comparison, count, limit
):
    login(client, data.admin)
    add_panels(comparison, count)
    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = {
        **setting.limits_json,
        limit: count - 1 if limit == "admin" else count,
    }
    if limit == "total":
        setting.limits_json = {
            **setting.limits_json,
            "total": count + 1,
            "admin": count,
            "openai": count + 2,
        }
    await data.db.commit()
    classroom = None
    gate = asyncio.Event()
    if limit == "total":
        login(client, data.owner)
        student.stream.events.insert(0, gate)
        classroom = asyncio.create_task(
            client.post(
                f"/sessions/{data.session.id}/turns/stream",
                json={
                    "request_id": str(uuid4()),
                    "content": "Classroom capacity",
                },
            )
        )
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        login(client, data.admin)
    try:
        response = await client.post("/admin/comparisons", json=comparison)
        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "1"
        assert student.responses.create.await_count == int(
            classroom is not None
        )
        assert (
            len(active_calls)
            == len(registered_calls)
            == int(classroom is not None)
        )
        async with data.factory() as db:
            assert (
                await db.scalar(select(func.count()).select_from(ComparisonRun))
                == 0
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GenerationRun)
                    .where(GenerationRun.comparison_run_id.is_not(None))
                )
                == 0
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ApiUsageLog)
                    .where(ApiUsageLog.usage_scope == "comparison")
                )
                == 0
            )
    finally:
        gate.set()
        if classroom is not None:
            assert (await classroom).status_code == 200
    assert not active_calls and not registered_calls


@pytest.mark.parametrize("count", [2, 3])
async def test_panels_share_context_with_distinct_options_and_per_run_seq(
    data, client, student, comparison, count
):
    login(client, data.admin)
    add_panels(comparison, count)
    streams = panel_streams(student, comparison)
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    events = frames(response)
    accepted = [value for kind, value in events if kind == "run.accepted"]
    assert len(accepted) == count
    assert len({value["context_hash"] for value in accepted}) == 1
    assert len({value["turn_id"] for value in accepted}) == 1
    assert len({value["run_id"] for value in accepted}) == count
    for panel in accepted:
        panel_events = [
            (kind, value)
            for kind, value in events
            if value["run_id"] == panel["run_id"]
        ]
        assert [kind for kind, _ in panel_events] == [
            "run.accepted",
            "output.delta",
            "output.delta",
            "output.completed",
        ]
        assert [value["seq"] for _, value in panel_events] == [0, 1, 2, 3]
        assert all(
            value["panel_id"] == panel["panel_id"]
            and value["turn_id"] == panel["turn_id"]
            for _, value in panel_events
        )
    calls = [call.kwargs for call in student.responses.create.call_args_list]
    assert len(calls) == count
    assert all(
        call["input"] == calls[0]["input"]
        and call["instructions"] == calls[0]["instructions"]
        for call in calls
    )
    assert {call["max_output_tokens"] for call in calls} == set(
        range(1500, 1500 + count)
    )
    state = (
        await client.get(
            f"/admin/comparisons/{accepted[0]['comparison_run_id']}"
        )
    ).json()
    assert state["status"] == "finished"
    assert all(
        panel["status"] == "completed" and panel["text"] == "Student answer"
        for panel in state["panels"]
    )
    assert [
        panel["applied_options"]["max_output_tokens"]
        for panel in state["panels"]
    ] == list(range(1500, 1500 + count))
    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).json() == state
    assert student.responses.create.await_count == count
    assert all(stream.closed for stream in streams)
    assert student.close.await_count == count
    assert not active_calls and not registered_calls


@pytest.mark.parametrize("failure", ["options", "missing_model", "budget"])
async def test_last_panel_preflight_failure_blocks_every_panel(
    data, client, student, comparison, failure
):
    login(client, data.admin)
    add_panels(comparison, 3)
    last = comparison["panels"][-1]["model_config"]
    if failure == "options":
        last["options"]["temperature"] = 0.4
    elif failure == "missing_model":
        last["model_config_id"] = 99999
    else:
        for message in comparison["canonical_context"]:
            message["content"] = "길" * 45000
        comparison["current_message"] = "x" * 5000
        last["options"]["max_output_tokens"] = 128000
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == (
        503 if failure == "missing_model" else 422
    ), response.text
    if failure != "missing_model":
        assert response.json()["detail"]["errors"][0]["panel_id"] == "3"
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        for model in (ComparisonRun, GenerationRun, ApiUsageLog):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(
    "failure", ["provider", "429", "refusal", "ledger", "commit"]
)
async def test_admitted_panel_failure_does_not_cancel_or_retry_siblings(
    data, client, student, comparison, failure
):
    import httpx2
    from openai import RateLimitError
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import SQLAlchemyError

    login(client, data.admin)
    add_panels(comparison, 3)
    streams = panel_streams(student, comparison)
    if failure == "provider":
        streams[0].events = [
            streams[0].events[0],
            RuntimeError("PRIVATE_PROVIDER_ERROR"),
        ]
    elif failure == "429":

        def create(**kwargs):
            if kwargs["max_output_tokens"] == 1500:
                raise RateLimitError(
                    "PRIVATE_PROVIDER_ERROR",
                    response=httpx2.Response(
                        429,
                        request=httpx2.Request("POST", "https://mock.invalid"),
                    ),
                    body={"code": "rate_limit_exceeded"},
                )
            return streams[kwargs["max_output_tokens"] - 1500]

        student.responses.create.side_effect = create
    elif failure == "refusal":
        from types import SimpleNamespace

        streams[0].events[-1].type = "response.incomplete"
        streams[0].events[-1].response.status = "incomplete"
        streams[0].events[-1].response.incomplete_details = SimpleNamespace(
            reason="content_filter"
        )
    else:
        failed = False

        def unavailable(conn, cursor, statement, parameters, context, many):
            nonlocal failed
            run_id = None
            if failure == "ledger" and statement.startswith(
                "INSERT INTO api_usage_log"
            ):
                run_id = context.compiled_parameters[0]["run_id"]
            elif (
                failure == "commit"
                and statement.startswith("UPDATE generation_run")
                and "completed" in parameters
            ):
                run_id = parameters[-1]
            if (
                run_id is not None
                and not failed
                and conn.exec_driver_sql(
                    "SELECT panel_id FROM generation_run WHERE id=?", (run_id,)
                ).scalar()
                == "1"
            ):
                failed = True
                raise SQLAlchemyError("PRIVATE_STORAGE_ERROR")

        sql_event.listen(
            data.engine.sync_engine, "before_cursor_execute", unavailable
        )
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 200
    events = frames(response)
    accepted = next(value for kind, value in events if kind == "run.accepted")
    state = (
        await client.get(f"/admin/comparisons/{accepted['comparison_run_id']}")
    ).json()
    assert state["status"] == "finished"
    assert [panel["status"] for panel in state["panels"]] == [
        "interrupted" if failure == "commit" else "failed",
        "completed",
        "completed",
    ]
    assert all(
        panel["text"] == "Student answer" for panel in state["panels"][1:]
    )
    assert any(
        kind == ("run.interrupted" if failure == "commit" else "run.failed")
        and value["panel_id"] == "1"
        for kind, value in events
    )
    assert sum(kind == "output.completed" for kind, _ in events) == 2
    if failure in ("429", "refusal"):
        assert state["panels"][0]["error_code"] == (
            "rate_limited" if failure == "429" else "refused"
        )
    assert (
        "PRIVATE_PROVIDER_ERROR" not in response.text
        and "PRIVATE_STORAGE_ERROR" not in response.text
    )
    assert student.responses.create.await_count == (
        2 if failure == "ledger" else 3
    )
    assert all(stream.closed for stream in streams[1:])
    assert not active_calls and not registered_calls


@pytest.mark.parametrize("same_request", [True, False])
async def test_simultaneous_starts_have_one_admitted_comparison(
    data, client, student, comparison, same_request
):
    login(client, data.admin)
    add_panels(comparison, 3)
    streams = panel_streams(student, comparison)
    release = asyncio.Event()
    for stream in streams:
        stream.events.insert(0, release)
    other = (
        comparison
        if same_request
        else {**comparison, "request_id": str(uuid4())}
    )
    tasks = [
        asyncio.create_task(client.post("/admin/comparisons", json=body))
        for body in (comparison, other)
    ]
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED
        )
        assert len(done) == 1
        refused_or_replayed = next(iter(done)).result()
        assert refused_or_replayed.status_code == (200 if same_request else 409)
        if same_request:
            assert refused_or_replayed.headers["content-type"].startswith(
                "application/json"
            )
            assert refused_or_replayed.json()["status"] == "running"
        for stream in streams:
            await asyncio.wait_for(stream.read_started.wait(), 10)
        assert student.responses.create.await_count == len(active_calls) == 3
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert (
        sum(
            result.headers["content-type"].startswith("text/event-stream")
            for result in results
        )
        == 1
    )
    assert student.responses.create.await_count == 3
    async with data.factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(ComparisonRun))
            == 1
        )
        assert (
            await db.scalar(select(func.count()).select_from(GenerationRun))
            == 3
        )
        assert (
            await db.scalar(select(func.count()).select_from(ApiUsageLog)) == 3
        )
    assert not active_calls and not registered_calls

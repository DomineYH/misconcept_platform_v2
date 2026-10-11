"""Comparison interruption, permission loss and durable execution boundaries."""

import asyncio
import json

import pytest
from sqlalchemy import select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ComparisonRun, GenerationRun
from src.services.call_admission import active_calls
from src.services.generation_lifecycle import active_runs, interrupt_orphans

__all__ = ["client", "comparison", "scenario_payload", "student"]


async def test_comparison_records_provider_identity(
    data, client, student, comparison
):
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    state = (
        await client.get(f"/admin/comparisons/{accepted['comparison_run_id']}")
    ).json()
    assert state["panels"][0]["provider_model_id"] == "gpt-5-mini"
    assert state["panels"][0]["provider_request_id"] == "resp_probe"


@pytest.mark.parametrize("disconnect_when", ["headers", "delta", "completed"])
async def test_comparison_disconnect_preserves_committed_state_and_releases_slot(
    data, client, student, comparison, disconnect_when
):
    from src.main import app

    login(client, data.admin)
    disconnect = asyncio.Event()
    requested = False
    received = []
    if disconnect_when == "delta":
        student.stream.events.insert(1, asyncio.Event())

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return dict(
                type="http.request",
                body=json.dumps(comparison).encode(),
                more_body=False,
            )
        await disconnect.wait()
        return dict(type="http.disconnect")

    async def send(message):
        if (
            message["type"] == "http.response.start"
            and disconnect_when == "headers"
        ):
            disconnect.set()
            await asyncio.Event().wait()
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            received.append(body)
            if (disconnect_when == "delta" and b"output.delta" in body) or (
                disconnect_when == "completed" and b"output.completed" in body
            ):
                disconnect.set()
                await asyncio.Event().wait()

    scope = dict(
        type="http",
        asgi={"version": "3.0", "spec_version": "2.0"},
        http_version="1.1",
        method="POST",
        scheme="http",
        path="/admin/comparisons",
        raw_path=b"/admin/comparisons",
        query_string=b"",
        root_path="",
        headers=[
            (b"content-type", b"application/json"),
            (b"cookie", f"session_id={client.cookies['session_id']}".encode()),
        ],
        client=("127.0.0.1", 9000),
        server=("test", 80),
    )
    await asyncio.wait_for(app(scope, receive, send), 10)
    async with data.factory() as db:
        run = (await db.scalars(select(GenerationRun))).one()
        comparison_id = run.comparison_run_id
    state = (await client.get(f"/admin/comparisons/{comparison_id}")).json()
    assert state["status"] == "finished"
    assert state["panels"][0]["status"] == (
        "completed" if disconnect_when == "completed" else "interrupted"
    )
    assert student.responses.create.await_count == (
        0 if disconnect_when == "headers" else 1
    )
    assert not active_calls and not active_runs
    if disconnect_when == "delta":
        assert state["panels"][0]["text"] == "Student "
        assert student.stream.closed


@pytest.mark.parametrize("change", ["admin_role", "connection"])
async def test_comparison_rechecks_permission_before_completion(
    data, client, student, comparison, change
):
    login(client, data.admin)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    task = asyncio.create_task(
        client.post("/admin/comparisons", json=comparison)
    )
    try:
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        if change == "admin_role":
            data.admin.role = "teacher"
        else:
            student.connection.enabled = False
        await data.db.commit()
    finally:
        gate.set()
    result = await task
    assert frames(result)[-1][0] != "output.completed"
    async with data.factory() as db:
        run = (await db.scalars(select(GenerationRun))).one()
        assert (
            run.status in ("failed", "interrupted") and run.outcome_json is None
        )
    assert not active_calls


async def test_comparison_restart_finishes_aggregate_without_generation(
    data, client, student, comparison
):
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    async with data.factory() as db:
        run = await db.get(GenerationRun, accepted["run_id"])
        run.status, run.outcome_json = "running", None
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.status = "running"
        await db.commit()
    await interrupt_orphans(data.factory)
    state = (
        await client.get(f"/admin/comparisons/{accepted['comparison_run_id']}")
    ).json()
    assert (
        state["status"] == "finished"
        and state["panels"][0]["status"] == "interrupted"
    )
    assert student.responses.create.await_count == 1


@pytest.mark.parametrize("count", [1, 3])
async def test_comparison_reservation_commit_failure_calls_no_provider(
    data, client, student, comparison, monkeypatch, count
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import SQLAlchemyError
    from test_comparison_concurrency import add_panels

    login(client, data.admin)
    add_panels(comparison, count)

    def reject(conn):
        raise SQLAlchemyError("PRIVATE_COMMIT_FAILURE")

    sql_event.listen(data.engine.sync_engine, "commit", reject)
    try:
        response = await client.post("/admin/comparisons", json=comparison)
    finally:
        sql_event.remove(data.engine.sync_engine, "commit", reject)
    assert (
        response.status_code == 503
        and "PRIVATE_COMMIT_FAILURE" not in response.text
    )
    student.responses.create.assert_not_awaited()
    assert not active_calls
    async with data.factory() as db:
        assert (await db.scalars(select(ComparisonRun))).all() == []


async def test_comparison_revoked_admin_before_sdk_is_zero_calls(
    data, client, student, comparison
):
    from sqlalchemy import event as sql_event

    def revoke_after_ledger(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO api_usage_log"):
            conn.exec_driver_sql(
                "UPDATE user SET role='teacher' WHERE id=?", (data.admin.id,)
            )

    sql_event.listen(
        data.engine.sync_engine, "after_cursor_execute", revoke_after_ledger
    )
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    assert frames(result)[-1][0] == "run.interrupted"
    student.responses.create.assert_not_awaited()
    assert not active_calls


async def test_retry_is_cancelled_at_retention_deadline_without_extending_expiry(
    data, client, student, comparison
):
    from datetime import timedelta
    from uuid import uuid4

    from src.services.comparison_state import utcnow

    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    async with data.factory() as db:
        run = await db.get(GenerationRun, accepted["run_id"])
        run.status, run.outcome_json = "failed", None
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.expires_at = utcnow() + timedelta(seconds=1)
        record.created_at = record.expires_at - timedelta(days=30)
        expires = record.expires_at
        await db.commit()
    gate = asyncio.Event()
    student.stream.events = [gate]
    try:
        result = await asyncio.wait_for(
            client.post(
                f"{path}/panels/1/retry",
                json={
                    "request_id": str(uuid4()),
                    "generation_id": accepted["run_id"],
                },
            ),
            3,
        )
    finally:
        gate.set()
    assert frames(result)[-1][0] == "run.interrupted"
    assert (await client.get(path)).status_code == 404
    assert (await client.get("/admin/comparisons")).json()["items"] == []
    assert (await client.delete(path)).status_code == 404
    assert (
        await client.get(
            "/admin/scenarios/new",
            params={"comparison_id": accepted["comparison_run_id"]},
        )
    ).status_code == 404
    async with data.factory() as db:
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        assert record.expires_at == expires and record.status == "finished"
        runs = (
            await db.scalars(
                select(GenerationRun).order_by(GenerationRun.started_at)
            )
        ).all()
        assert [run.status for run in runs] == ["failed", "interrupted"]
    assert not active_calls and not active_runs

"""Session closure and startup recovery at HTTP/lifespan boundaries."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import frames
from test_student_generation import student as student_fixture

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture


@pytest.mark.parametrize("entry", ["close", "end", "admin", "manager"])
async def test_end_cancels_running_student_before_upstream_finishes(
    data, client, student, monkeypatch, entry
):
    login(client, data.owner)
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def create(**kwargs):
        entered.set()
        return student.stream

    student.responses.create.side_effect = create
    student.stream.events.insert(0, gate)
    payload = {"request_id": str(uuid4()), "content": "End while waiting"}
    path = f"/sessions/{data.session.id}"
    task = asyncio.create_task(
        client.post(f"{path}/turns/stream", json=payload)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        from src.models import ApiUsageLog, GenerationRun

        async with data.factory() as db:
            attempt = (await db.scalars(select(ApiUsageLog))).one()
            budget = attempt.context_budget_json
            assert budget and attempt.status == "running"

        running = (
            await client.get(
                f"{path}/runs", params={"request_id": payload["request_id"]}
            )
        ).json()
        mentor_id = str(uuid4())
        data.db.add(
            GenerationRun(
                id=mentor_id,
                owner_id=data.owner.id,
                session_id=data.session.id,
                turn_id=running["turn_id"],
                operation="mentor",
                request_id=str(uuid4()),
                input_hash="a" * 64,
                config_hash="b" * 64,
                provider="openai",
                model="test",
                status="running",
            )
        )
        await data.db.commit()
        if entry == "manager":
            from src.services.session_mgr import SessionManager

            await SessionManager(data.db, data.session.id).end_session()
        elif entry == "admin":
            login(client, data.admin)
            # End must survive unavailable analysis.
            from src.api.routes import admin_session_actions

            async def unavailable(*args):
                raise RuntimeError("Analysis unavailable")

            monkeypatch.setattr(
                admin_session_actions, "analyze_session", unavailable
            )
            response = await client.post(f"/admin{path}/end")
        else:
            response = await client.post(f"{path}/{entry}")
        if entry != "manager":
            assert response.status_code == 200
        login(client, data.owner)
        snapshot = (
            await client.get(
                f"{path}/runs", params={"request_id": payload["request_id"]}
            )
        ).json()
        assert snapshot["status"] == "cancelled"
        assert snapshot["error_code"] == "session_ended"
        assert snapshot["retryable"] is False
        assert snapshot["message"] is None
        mentor = (await client.get(f"/runs/{mentor_id}")).json()
        assert mentor["status"] == "cancelled"
        assert mentor["retryable"] is False
        assert mentor["message"] is None
        # End cancels the blocked upstream read without releasing its fake gate.
        result = await asyncio.wait_for(asyncio.shield(task), 2)
        assert frames(result)[-1][0] == "run.cancelled"
        assert student.stream.closed
        student.close.assert_awaited_once()
        assert (
            await client.post(
                f"{path}/turns/stream",
                json={
                    **payload,
                    "request_id": str(uuid4()),
                    "turn_id": snapshot["turn_id"],
                },
            )
        ).status_code == 400
        assert student.responses.create.await_count == 1
        async with data.factory() as db:
            attempt = (await db.scalars(select(ApiUsageLog))).one()
            assert attempt.context_budget_json == budget
            assert attempt.finished_at is not None
            assert (
                attempt.input_tokens is None
                and attempt.estimated_cost_usd is None
            )
        assert "context_budget" not in str(snapshot)
    finally:
        gate.set()
        await task


@pytest.mark.parametrize("recovery_commit_fails", [False, True])
async def test_startup_interrupts_orphans_without_generating(
    data, client, student, monkeypatch, recovery_commit_fails
):
    from unittest.mock import AsyncMock

    from src import main
    from src.models import GenerationRun, Message

    login(client, data.owner)
    path = f"/sessions/{data.session.id}"
    completed = await client.post(
        f"{path}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Already saved"},
    )
    assert frames(completed)[-1][0] == "output.completed"
    completed_id = frames(completed)[0][1]["run_id"]
    turn_id = str(uuid4())
    data.db.add(
        Message(
            session_id=data.session.id,
            role="teacher",
            content="Orphan question",
            turn_id=turn_id,
            turn_index=2,
        )
    )
    orphans = []
    for operation, status in [
        ("student", "running"),
        ("mentor", "running"),
        ("student", "failed"),
    ]:
        run = GenerationRun(
            id=str(uuid4()),
            owner_id=data.owner.id,
            session_id=data.session.id,
            turn_id=turn_id,
            operation=operation,
            request_id=str(uuid4()),
            input_hash="a" * 64,
            config_hash="b" * 64,
            provider="openai",
            model="test",
            status=status,
            partial_text="Previously saved partial",
            error_code="provider_error" if status == "failed" else None,
        )
        data.db.add(run)
        orphans.append((run.id, status))
    await data.db.commit()
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(main, "close_db", AsyncMock())
    # Bind the real startup path to the isolated file database.
    from src.db import connection

    monkeypatch.setattr(connection, "AsyncSessionLocal", data.factory)
    if recovery_commit_fails:
        from sqlalchemy import event as sql_event

        def reject_recovery(conn):
            raise RuntimeError("Injected startup commit failure")

        sql_event.listen(data.engine.sync_engine, "commit", reject_recovery)
        try:
            with pytest.raises(
                RuntimeError, match="Injected startup commit failure"
            ):
                async with main.app.router.lifespan_context(main.app):
                    pytest.fail(
                        "Startup must not serve before recovery commits"
                    )
        finally:
            sql_event.remove(data.engine.sync_engine, "commit", reject_recovery)
        for run_id, before in orphans:
            assert (await client.get(f"/runs/{run_id}")).json()[
                "status"
            ] == before
    async with main.app.router.lifespan_context(main.app):
        for run_id, before in orphans:
            state = (await client.get(f"/runs/{run_id}")).json()
            assert state["status"] == (
                "interrupted" if before == "running" else "failed"
            )
            assert state["error_code"] == (
                "server_restarted" if before == "running" else "provider_error"
            )
            assert state["message"] is None
            assert state["partial_text"] == "Previously saved partial"
        assert (await client.get(f"/runs/{completed_id}")).json()[
            "status"
        ] == "completed"
        assert student.responses.create.await_count == 1
    # Startup is idempotent and preserves terminal states on another boot.
    async with main.app.router.lifespan_context(main.app):
        assert (await client.get(f"/runs/{orphans[0][0]}")).json()[
            "status"
        ] == "interrupted"
        assert student.responses.create.await_count == 1
    interrupted = (await client.get(f"/runs/{orphans[0][0]}")).json()
    retry = await client.post(
        f"{path}/turns/stream",
        json={
            "request_id": str(uuid4()),
            "content": "Orphan question",
            "turn_id": turn_id,
        },
    )
    accepted = frames(retry)[0][1]
    assert accepted["teacher_message_id"] == interrupted["teacher_message_id"]
    assert accepted["turn_id"] == turn_id
    assert accepted["turn_index"] == 2
    assert accepted["run_id"] != orphans[0][0]
    assert frames(retry)[-1][0] == "output.completed"
    assert student.responses.create.await_count == 2


async def test_end_commit_failure_preserves_open_session_and_running_rights(
    data, client, student
):
    from sqlalchemy import event as sql_event

    from src.models import Session

    login(client, data.owner)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        entered.set()
        return student.stream

    student.responses.create.side_effect = create
    student.stream.events.insert(0, gate)
    path = f"/sessions/{data.session.id}"
    payload = {"request_id": str(uuid4()), "content": "Atomic end"}
    task = asyncio.create_task(
        client.post(f"{path}/turns/stream", json=payload)
    )
    ending = False

    def detect_end(conn, cursor, statement, parameters, context, many):
        nonlocal ending
        if statement.startswith("UPDATE session SET ended_at"):
            ending = True

    def reject_end(conn):
        nonlocal ending
        if ending:
            ending = False
            raise RuntimeError("Injected end commit failure")

    try:
        await asyncio.wait_for(entered.wait(), 3)
        sql_event.listen(
            data.engine.sync_engine, "before_cursor_execute", detect_end
        )
        sql_event.listen(data.engine.sync_engine, "commit", reject_end)
        try:
            with pytest.raises(
                RuntimeError, match="Injected end commit failure"
            ):
                await client.post(f"{path}/close")
        finally:
            sql_event.remove(
                data.engine.sync_engine, "before_cursor_execute", detect_end
            )
            sql_event.remove(data.engine.sync_engine, "commit", reject_end)
        state = (
            await client.get(
                f"{path}/runs", params={"request_id": payload["request_id"]}
            )
        ).json()
        assert state["status"] == "running"
        assert state["message"] is None
        async with data.factory() as reader:
            assert (await reader.get(Session, data.session.id)).ended_at is None
        assert not student.stream.closed
        assert (await client.post(f"{path}/close")).json()[
            "already_ended"
        ] is False
        assert (
            frames(await asyncio.wait_for(asyncio.shield(task), 2))[-1][0]
            == "run.cancelled"
        )
    finally:
        gate.set()
        await task


async def test_end_between_reservation_and_response_start_skips_upstream(
    data, client, student
):
    from src.api.routes.student_generation import StudentRequest
    from src.services.generation_runs import reserve_student
    from src.services.student_stream import StudentStreamingResponse

    login(client, data.owner)
    accepted, kwargs = await reserve_student(
        data.factory,
        data.session.id,
        data.owner,
        StudentRequest(request_id=str(uuid4()), content="End before handoff"),
    )
    assert (
        await client.post(f"/sessions/{data.session.id}/close")
    ).status_code == 200
    # The end committed before a response could register its in-memory signal.
    response = StudentStreamingResponse(data.factory, accepted, kwargs)
    received = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            received.append(message.get("body", b""))

    await asyncio.wait_for(
        response(
            {"type": "http", "asgi": {"spec_version": "2.0"}},
            receive,
            send,
        ),
        3,
    )
    assert b"run.cancelled" in b"".join(received)
    assert b"output.completed" not in b"".join(received)
    student.responses.create.assert_not_awaited()
    snapshot = (await client.get(f"/runs/{accepted['run_id']}")).json()
    assert snapshot["status"] == "cancelled"
    assert snapshot["message"] is None

"""Mentor timeout, disconnect and atomic end/finalization behavior."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event as sql_event
from test_mentor_generation import (
    client,
    complete_turn,
    mentor,
    mentor_url,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.services import mentor_stream

__all__ = ["client", "mentor", "scenario_payload", "student"]


async def waiting_mentor(data, client, mentor):
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    mentor.responses.create.side_effect = create
    mentor.close.reset_mock()
    return turn, entered, cancelled


async def test_mentor_deadline_heartbeats_and_cleanup(
    data, client, mentor, monkeypatch
):
    turn, entered, cancelled = await waiting_mentor(data, client, mentor)
    monkeypatch.setattr(mentor_stream, "RUN_SECONDS", 0.2)
    monkeypatch.setattr(mentor_stream, "HEARTBEAT_SECONDS", 0.02)
    response = await client.post(
        mentor_url(data, turn), json={"request_id": str(uuid4())}
    )
    assert entered.is_set()
    assert ": ping\n\n" in response.text
    assert [kind for kind, _ in frames(response)] == [
        "run.accepted",
        "run.failed",
    ]
    assert frames(response)[-1][1]["code"] == "run_timeout"
    assert cancelled.is_set()
    mentor.close.assert_awaited_once()
    snapshot = (
        await client.get(f"/runs/{frames(response)[0][1]['run_id']}")
    ).json()
    assert snapshot["status"] == "failed"
    assert snapshot["message"] is None
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 0


@pytest.mark.parametrize("entry", ["close", "end", "admin"])
async def test_end_cancels_waiting_mentor_without_coaching_or_increment(
    data,
    client,
    mentor,
    entry,
    monkeypatch,
):
    turn, entered, cancelled = await waiting_mentor(data, client, mentor)
    login(client, data.owner)
    payload = {"request_id": str(uuid4())}
    pending = asyncio.create_task(
        client.post(mentor_url(data, turn), json=payload)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if entry == "admin":
            from src.api.routes import admin_session_actions

            async def unavailable(*args):
                raise RuntimeError("Analysis unavailable")

            monkeypatch.setattr(
                admin_session_actions, "analyze_session", unavailable
            )
            login(client, data.admin)
            end = await client.post(f"/admin/sessions/{data.session.id}/end")
        else:
            end = await client.post(f"/sessions/{data.session.id}/{entry}")
        assert end.status_code == 200
        result = await asyncio.wait_for(asyncio.shield(pending), 3)
        assert frames(result)[-1][0] == "run.cancelled"
        assert cancelled.is_set()
        mentor.close.assert_awaited_once()
        login(client, data.owner)
        snapshot = (
            await client.get(f"/runs/{frames(result)[0][1]['run_id']}")
        ).json()
        assert snapshot["status"] == "cancelled"
        assert snapshot["message"] is None
        assert snapshot["retryable"] is False
        await data.db.refresh(data.session)
        assert data.session.ended_at is not None
        assert data.session.tutor_question_count == 1
        assert data.session.tutor_intervention_count == 0
    finally:
        pending.cancel()
        if not pending.done():
            await asyncio.gather(pending, return_exceptions=True)


async def test_coaching_commit_failure_rolls_back_intervention_count(
    data,
    client,
    mentor,
):
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    pending_commit = False

    def coach_insert(conn, cursor, statement, parameters, context, many):
        nonlocal pending_commit
        if (
            statement.startswith("INSERT INTO message")
            and "tutor" in parameters
        ):
            pending_commit = True

    def reject_commit(conn):
        nonlocal pending_commit
        if pending_commit:
            pending_commit = False
            raise RuntimeError("Injected coaching commit failure")

    sql_event.listen(
        data.engine.sync_engine, "before_cursor_execute", coach_insert
    )
    sql_event.listen(data.engine.sync_engine, "commit", reject_commit)
    try:
        response = await client.post(
            mentor_url(data, turn), json={"request_id": str(uuid4())}
        )
    finally:
        sql_event.remove(
            data.engine.sync_engine, "before_cursor_execute", coach_insert
        )
        sql_event.remove(data.engine.sync_engine, "commit", reject_commit)
    assert frames(response)[-1][0] == "run.failed"
    assert frames(response)[-1][1]["code"] == "storage_error"
    await data.db.refresh(data.session)
    assert data.session.tutor_intervention_count == 0
    assert data.session.tutor_question_count == 1
    snapshot = (
        await client.get(f"/runs/{frames(response)[0][1]['run_id']}")
    ).json()
    assert snapshot["message"] is None
    retry = await client.post(
        mentor_url(data, turn), json={"request_id": str(uuid4())}
    )
    assert frames(retry)[-1][0] == "output.completed"
    await data.db.refresh(data.session)
    assert data.session.tutor_intervention_count == 1
    assert data.session.tutor_question_count == 1


@pytest.mark.parametrize("disconnect_when", ["headers", "accepted", "upstream"])
async def test_disconnect_interrupts_mentor_and_closes_sdk(
    data,
    client,
    mentor,
    disconnect_when,
):
    from src.main import app

    turn, entered, cancelled = await waiting_mentor(data, client, mentor)
    login(client, data.owner)
    payload = {"request_id": str(uuid4())}
    import json

    body = json.dumps(payload).encode()
    disconnected = asyncio.Event()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect_when == "upstream":
            await entered.wait()
        else:
            await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if (
            disconnect_when == "headers"
            and message["type"] == "http.response.start"
        ):
            disconnected.set()
        if disconnect_when == "accepted" and b"run.accepted" in message.get(
            "body", b""
        ):
            disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": mentor_url(data, turn),
        "raw_path": mentor_url(data, turn).encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "headers": [
            (b"content-type", b"application/json"),
            (
                b"cookie",
                f"session_id={client.cookies.get('session_id')}".encode(),
            ),
        ],
    }
    await asyncio.wait_for(app(scope, receive, send), 3)
    state = (
        await client.get(
            f"/sessions/{data.session.id}/runs",
            params={"request_id": payload["request_id"]},
        )
    ).json()
    assert state["status"] == "interrupted"
    assert state["error_code"] == "disconnected"
    assert state["message"] is None
    if disconnect_when == "upstream":
        assert cancelled.is_set()
        mentor.close.assert_awaited_once()
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 0


async def test_end_committed_before_handoff_never_opens_mentor_sdk(
    data,
    client,
    mentor,
):
    from src.services.mentor_generation import reserve_mentor

    login(client, data.owner)
    turn = await complete_turn(client, data)
    accepted, execution = await reserve_mentor(
        data.factory,
        data.session.id,
        turn["turn_id"],
        data.owner,
        str(uuid4()),
    )
    await client.post(f"/sessions/{data.session.id}/close")
    mentor.responses.create.reset_mock()
    response = mentor_stream.mentor_response(data.factory, accepted, execution)
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
    mentor.responses.create.assert_not_awaited()
    await data.db.refresh(data.session)
    assert data.session.tutor_intervention_count == 0


async def test_persisted_end_wins_even_when_live_cancel_signal_is_missed(
    data,
    client,
    mentor,
):
    from types import SimpleNamespace

    from src.services.generation_lifecycle import active_runs

    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        entered.set()
        await gate.wait()
        return SimpleNamespace(output_text="Too late coaching", usage=None)

    mentor.responses.create.side_effect = create
    payload = {"request_id": str(uuid4())}
    pending = asyncio.create_task(
        client.post(mentor_url(data, turn), json=payload)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        state = (
            await client.get(
                f"/sessions/{data.session.id}/runs",
                params=payload,
            )
        ).json()
        # A missed live signal must not allow coaching after persisted end.
        active_runs.pop(state["run_id"])
        assert (
            await client.post(f"/sessions/{data.session.id}/close")
        ).status_code == 200
        gate.set()
        result = await asyncio.wait_for(asyncio.shield(pending), 3)
        assert frames(result)[-1][0] == "run.cancelled"
        state = (await client.get(f"/runs/{state['run_id']}")).json()
        assert state["message"] is None
        await data.db.refresh(data.session)
        assert data.session.tutor_intervention_count == 0
        updates = await client.get(
            f"/sessions/{data.session.id}/messages/updates"
        )
        assert "Too late coaching" not in updates.text
    finally:
        gate.set()
        await pending

"""Provider and persistence failures through the real student HTTP route."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import event, frames
from test_student_generation import student as student_fixture

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture


@pytest.mark.parametrize(
    "mode,code",
    [
        ("empty", "empty_output"),
        ("refusal", "refused"),
        ("final_refusal", "refused"),
        ("incomplete", "incomplete_response"),
        ("failed", "transient"),
        ("error", "transient"),
        ("eof", "invalid_output"),
    ],
)
async def test_provider_failures_never_become_student_messages(
    data, client, student, mode, code
):
    login(client, data.owner)
    response = SimpleNamespace(
        status="completed", output_text=" ", output=[], usage=None
    )
    events = [event("response.output_text.delta", delta="Provisional")]
    if mode == "empty":
        events.append(event("response.completed", response=response))
    elif mode == "refusal":
        events.append(event("response.refusal.delta", delta="Refusal"))
    elif mode == "final_refusal":
        response.output_text = "Looks like an answer"
        response.output = [
            SimpleNamespace(
                type="message", content=[SimpleNamespace(type="refusal")]
            )
        ]
        events.append(event("response.completed", response=response))
    elif mode == "incomplete":
        response.status = "incomplete"
        response.incomplete_details = SimpleNamespace(
            reason="max_output_tokens"
        )
        response.output_text = "Incomplete answer"
        events.append(event("response.incomplete", response=response))
    elif mode == "failed":
        response.status = "failed"
        response.error = SimpleNamespace(code="server_error", message="Private")
        events.append(event("response.failed", response=response))
    elif mode == "error":
        events.append(
            event(
                "error", code="server_error", message="private upstream details"
            )
        )
    student.stream.events = events
    payload = {"request_id": str(uuid4()), "content": "Why?"}
    result = await client.post(
        f"/sessions/{data.session.id}/turns/stream", json=payload
    )
    assert frames(result)[-1][0] == "run.failed"
    assert frames(result)[-1][1]["code"] == code
    state = await client.get(
        f"/sessions/{data.session.id}/runs",
        params={"request_id": payload["request_id"]},
    )
    assert state.json()["message"] is None
    assert state.json()["partial_text"] == "Provisional"
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.text.count("data-message-id=") == 1
    assert "Provisional" not in updates.text
    assert student.responses.create.await_count == 1
    assert student.stream.closed
    student.close.assert_awaited_once()


async def test_final_commit_failure_never_emits_completed(
    data, client, student
):
    from sqlalchemy import event as sql_event

    pending_commit = False

    def student_insert(conn, cursor, statement, parameters, context, many):
        nonlocal pending_commit
        if (
            statement.startswith("INSERT INTO message")
            and "student" in parameters
        ):
            pending_commit = True

    def reject_completion(conn):
        nonlocal pending_commit
        if pending_commit:
            pending_commit = False
            raise RuntimeError("Injected SQLite commit failure")

    sql_event.listen(
        data.engine.sync_engine, "before_cursor_execute", student_insert
    )
    sql_event.listen(data.engine.sync_engine, "commit", reject_completion)
    login(client, data.owner)
    payload = {"request_id": str(uuid4()), "content": "Keep this teacher"}
    try:
        response = await client.post(
            f"/sessions/{data.session.id}/turns/stream", json=payload
        )
    finally:
        sql_event.remove(
            data.engine.sync_engine, "before_cursor_execute", student_insert
        )
        sql_event.remove(data.engine.sync_engine, "commit", reject_completion)
    assert frames(response)[-1][0] == "run.failed"
    assert frames(response)[-1][1]["code"] == "storage_error"
    state = await client.get(
        f"/sessions/{data.session.id}/runs",
        params={"request_id": payload["request_id"]},
    )
    assert state.json()["status"] == "failed"
    assert state.json()["message"] is None
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert "Keep this teacher" in updates.text
    assert updates.text.count("data-message-id=") == 1
    assert student.responses.create.await_count == 1


@pytest.mark.parametrize(
    "first_delta,code",
    [
        (False, "first_output_timeout"),
        (True, "run_timeout"),
    ],
)
async def test_deadlines_release_execution_without_saving_partial(
    data, client, student, monkeypatch, first_delta, code
):
    import asyncio

    from test_student_generation import FakeStream

    from src.models import AppSetting
    from src.services import student_stream

    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {
        **setting.timeouts_json,
        "student_first_output": 1,
        "student_total": 2,
    }
    await data.db.commit()
    monkeypatch.setattr(student_stream, "HEARTBEAT_SECONDS", 0.02)
    student.stream = FakeStream(
        (
            [event("response.output_text.delta", delta="Partial")]
            if first_delta
            else []
        )
        + [asyncio.Event()]
    )
    student.responses.create.return_value = student.stream
    login(client, data.owner)
    payload = {"request_id": str(uuid4()), "content": "Waiting question"}
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream", json=payload
    )
    assert ": ping\n\n" in response.text
    assert frames(response)[-1][1]["code"] == code
    state = await client.get(
        f"/sessions/{data.session.id}/runs",
        params={"request_id": payload["request_id"]},
    )
    assert state.json()["status"] == "failed"
    assert state.json()["message"] is None
    assert state.json()["partial_text"] == ("Partial" if first_delta else None)
    assert student.stream.closed
    student.close.assert_awaited_once()
    assert student.responses.create.await_count == 1


async def test_hidden_reasoning_does_not_suppress_heartbeat_or_reset_deadline(
    data, client, student, monkeypatch
):
    import asyncio

    from test_student_generation import FakeStream

    from src.services import student_stream

    class ReasoningStream(FakeStream):
        async def iterate(self):
            for _ in range(1000):
                await asyncio.sleep(0.005)
                yield event(
                    "response.reasoning_summary_text.delta",
                    delta="PRIVATE REASONING",
                )

    from src.models import AppSetting

    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {**setting.timeouts_json, "student_first_output": 1}
    await data.db.commit()
    monkeypatch.setattr(student_stream, "HEARTBEAT_SECONDS", 0.02)
    student.stream = ReasoningStream([])
    student.responses.create.return_value = student.stream
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Reasoning question"},
    )
    assert ": ping\n\n" in response.text
    assert "PRIVATE REASONING" not in response.text
    assert frames(response)[-1][1]["code"] == "first_output_timeout"
    assert student.stream.closed


@pytest.mark.parametrize(
    "slow_close,disconnect_when,cleanup_db",
    [
        (False, "delta", None),
        (True, "delta", None),
        ("stream", "delta", None),
        (False, "open", None),
        (False, "accepted", None),
        (False, "headers", None),
        (False, "completed", None),
        (False, "delta", "busy"),
    ],
)
async def test_disconnect_cancels_sdk_read_and_preserves_teacher(
    data, client, student, monkeypatch, slow_close, disconnect_when, cleanup_db
):
    import asyncio
    import json

    from test_student_generation import FakeStream

    from src.main import app

    if slow_close:
        from src.services import student_stream

        monkeypatch.setattr(student_stream, "CLEANUP_SECONDS", 0.5)
        if slow_close is True:
            student.close.side_effect = asyncio.Event().wait
    login(client, data.owner)
    gate = asyncio.Event()
    student.stream = FakeStream(
        [
            event(
                "response.output_text.delta", delta="Partial before disconnect"
            ),
            gate,
        ]
    )
    if disconnect_when == "completed":
        gate.set()
        student.stream.events.append(
            event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output_text="Saved before frame loss",
                    output=[],
                    usage=None,
                ),
            )
        )
    if slow_close == "stream":

        async def close():
            student.stream.closed = True
            await asyncio.Event().wait()

        student.stream.close = close
    student.responses.create.return_value = student.stream
    payload = {"request_id": str(uuid4()), "content": "Keep after disconnect"}
    disconnect = asyncio.Event()
    if disconnect_when == "open":

        async def create(**kwargs):
            disconnect.set()
            return student.stream

        student.responses.create.side_effect = create
    requested = False
    received = []
    writer = None
    if cleanup_db == "busy":
        from sqlalchemy import event as sql_event
        from sqlalchemy import text

        from src.services import student_stream

        monkeypatch.setattr(student_stream, "CLEANUP_SECONDS", 0.05)

        def short_busy_timeout(connection, record, proxy):
            cursor = connection.cursor()
            cursor.execute("PRAGMA busy_timeout=200")
            cursor.close()

        sql_event.listen(
            data.engine.sync_engine, "checkout", short_busy_timeout
        )

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {
                "type": "http.request",
                "body": json.dumps(payload).encode(),
                "more_body": False,
            }
        await disconnect.wait()
        if disconnect_when == "delta":
            await student.stream.read_started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal writer
        if (
            disconnect_when == "headers"
            and message["type"] == "http.response.start"
        ):
            disconnect.set()
            await asyncio.Event().wait()
        if message["type"] == "http.response.body":
            if (
                disconnect_when == "accepted"
                and b"run.accepted" in message.get("body", b"")
            ):
                disconnect.set()
                await asyncio.Event().wait()
            if (
                disconnect_when == "completed"
                and b"output.completed" in message.get("body", b"")
            ):
                disconnect.set()
                # Lose the frame after its DB commit.
                await asyncio.Event().wait()
            received.append(message.get("body", b""))
            if b"output.delta" in message.get("body", b""):
                if cleanup_db == "busy":
                    writer = await data.engine.connect()
                    await writer.execute(text("BEGIN IMMEDIATE"))
                if disconnect_when == "delta":
                    disconnect.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/sessions/{data.session.id}/turns/stream",
        "raw_path": f"/sessions/{data.session.id}/turns/stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"cookie", f"session_id={client.cookies['session_id']}".encode()),
        ],
        "client": ("127.0.0.1", 9000),
        "server": ("test", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), 5)
    finally:
        if writer is not None:
            await writer.rollback()
            await writer.close()
        if cleanup_db == "busy":
            sql_event.remove(
                data.engine.sync_engine, "checkout", short_busy_timeout
            )
    assert b"output.completed" not in b"".join(received)
    assert student.stream.closed == (
        disconnect_when not in {"headers", "accepted"}
    )
    if disconnect_when == "delta":
        assert student.stream.read_cancelled
    if disconnect_when in {"headers", "accepted"}:
        student.close.assert_not_awaited()
    else:
        student.close.assert_awaited_once()
    state = await client.get(
        f"/sessions/{data.session.id}/runs",
        params={"request_id": payload["request_id"]},
    )
    assert state.json()["status"] == (
        "completed"
        if disconnect_when == "completed"
        else "running" if cleanup_db else "interrupted"
    )
    assert state.json()["partial_text"] == (
        "Partial before disconnect"
        if disconnect_when == "delta" and not cleanup_db
        else None
    )
    if disconnect_when == "completed":
        assert state.json()["message"]["content"] == "Saved before frame loss"
        assert (
            await client.post(f"/sessions/{data.session.id}/close")
        ).status_code == 200
        assert (
            await client.get(f"/runs/{state.json()['run_id']}")
        ).json() == state.json()
        replay = await client.post(
            f"/sessions/{data.session.id}/turns/stream", json=payload
        )
        assert replay.json() == state.json()
    else:
        assert state.json()["message"] is None
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert "Keep after disconnect" in updates.text
    assert updates.text.count("data-message-id=") == (
        2 if disconnect_when == "completed" else 1
    )
    exported = await client.get(f"/sessions/{data.session.id}/export.csv")
    assert exported.status_code == 200
    assert "Partial before disconnect" not in exported.text
    assert student.responses.create.await_count == (
        0 if disconnect_when in {"headers", "accepted"} else 1
    )


async def test_transient_creation_error_has_no_automatic_retry(
    data, client, student
):
    import httpx2 as httpx
    from openai import APIConnectionError

    student.responses.create.side_effect = APIConnectionError(
        message="Private connection error",
        request=httpx.Request("POST", "https://provider.invalid/responses"),
    )
    login(client, data.owner)
    result = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Keep on network failure"},
    )
    assert frames(result)[-1][1]["code"] == "transient"
    assert "Private connection error" not in result.text
    assert student.responses.create.await_count == 1
    student.close.assert_awaited_once()


async def test_partial_output_is_excluded_from_csv_and_analysis_inputs(
    data, client, student, monkeypatch
):
    import json
    from copy import deepcopy

    import httpx2
    from test_analysis_invocations import analysis_transport
    from test_analysis_runs import terminal
    from test_student_probe import response_body

    from src.services.lesson_snapshots import canonical_hash

    login(client, data.owner)
    student.stream.events = [
        event("response.output_text.delta", delta="UNSAVED PARTIAL OUTPUT"),
        RuntimeError("Upstream interrupted"),
    ]
    path = f"/sessions/{data.session.id}"
    result = await client.post(
        f"{path}/turns/stream",
        json={
            "request_id": str(uuid4()),
            "content": "Preserved teacher question",
        },
    )
    assert frames(result)[-1][0] == "run.failed"
    exported = await client.get(f"{path}/export.csv")
    assert exported.status_code == 200
    assert "Preserved teacher question" in exported.text
    assert "UNSAVED PARTIAL OUTPUT" not in exported.text
    inputs = []

    student.model.verification_state = {
        **student.model.verification_state,
        "analysis": {
            **student.model.verification_state["student"],
            "role_contract_version": "s4-v2",
        },
    }
    envelope = deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"].update(
        classification_enabled=True,
        rubric_name="Test",
        rubric=[dict(id="A", name="A", criteria="Explore", level=None)],
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.commit()

    async def analysis_response(request, body):
        inputs.append(body["input"])
        return httpx2.Response(200, json=response_body("not json"))

    analysis_transport(monkeypatch, analysis_response)
    ended = await client.post(f"{path}/end")
    assert ended.status_code == 202
    await terminal(client, ended.json()["actions"]["status"])
    analyzed = await client.get(f"{path}/analysis")
    assert analyzed.status_code == 200
    assert analyzed.json()["feedback_status"] == "failed"
    assert len(inputs) == 1  # One analysis reviews all durable inputs.
    encoded = json.dumps(inputs)
    assert "Preserved teacher question" in encoded
    assert "UNSAVED PARTIAL OUTPUT" not in encoded

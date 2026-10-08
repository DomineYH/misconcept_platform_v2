"""Student turn contracts at authenticated HTTP + file SQLite boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from lesson_fixtures import LESSON_KEY, install_connection
from openai import AsyncOpenAI
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture

from src.services import base, openai_generation

client = client_fixture
scenario_payload = scenario_fixture


def event(kind, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False
        self.read_started = asyncio.Event()
        self.read_cancelled = False

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for item in self.events:
            if isinstance(item, Exception):
                raise item
            if isinstance(item, asyncio.Event):
                self.read_started.set()
                try:
                    await item.wait()
                except asyncio.CancelledError:
                    self.read_cancelled = True
                    raise
            else:
                yield item

    async def close(self):
        self.closed = True


@pytest.fixture
async def student(data, scenario_payload, monkeypatch):
    connection, model = await install_connection(data, monkeypatch)
    data.scenario.problem_situation = "Public problem"
    data.session.ended_at = None
    await data.db.commit()
    stream = FakeStream(
        [
            event("response.output_text.delta", delta="Student "),
            event("response.output_text.delta", delta="answer"),
            event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output_text="Student answer",
                    output=[],
                    usage=SimpleNamespace(
                        input_tokens=10, output_tokens=2, total_tokens=12
                    ),
                ),
            ),
        ]
    )
    fake = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=stream)),
        close=AsyncMock(),
        max_retries=0,
        stream=stream,
        connection=connection,
        model=model,
        sdk_options=[],
    )
    monkeypatch.setattr(base, "AsyncOpenAI", lambda **kw: fake)

    def serializable(value):
        if isinstance(value, SimpleNamespace):
            return {k: serializable(v) for k, v in vars(value).items()}
        if isinstance(value, list):
            return [serializable(v) for v in value]
        return value

    class TransportStream(httpx2.AsyncByteStream):
        def __init__(self, source):
            self.source = source

        async def __aiter__(self):
            async for item in self.source:
                values = serializable(item)
                values.setdefault("sequence_number", 1)
                values.setdefault("item_id", "msg")
                values.setdefault("output_index", 0)
                values.setdefault("content_index", 0)
                response = values.get("response")
                if response is not None:
                    from test_student_probe import response_body

                    body = response_body(response.pop("output_text", ""))
                    output = response.pop("output", [])
                    if output:
                        body["output"] = output
                    body.update(response)
                    values["response"] = body
                yield (
                    f"event: {item.type}\ndata: {json.dumps(values)}\n\n"
                ).encode()

        async def aclose(self):
            await self.source.close()

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {LESSON_KEY}"
        source = await fake.responses.create(**json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=TransportStream(source),
        )

    class SDK(AsyncOpenAI):
        async def close(self):
            await super().close()
            await fake.close()

    def sdk(**kwargs):
        fake.sdk_options.append(kwargs)
        assert kwargs["max_retries"] == 0
        return SDK(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", sdk)
    return fake


def frames(response):
    return [
        (frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:]))
        for frame in response.text.split("\n\n")
        if frame.startswith("event:")
    ]


async def test_student_stream_commits_one_turn_and_reuses_request(
    data, client, student
):
    login(client, data.owner)
    payload = {"request_id": str(uuid4()), "content": "Why?"}
    url = f"/sessions/{data.session.id}/turns/stream"
    response = await client.post(url, json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    events = frames(response)
    assert [kind for kind, _ in events] == [
        "run.accepted",
        "output.delta",
        "output.delta",
        "output.completed",
    ]
    assert [frame["seq"] for _, frame in events] == [0, 1, 2, 3]
    accepted, completed = events[0][1], events[-1][1]
    assert accepted["turn_index"] == 1
    assert completed["message"]["content"] == "Student answer"
    assert completed["message"]["role"] == "student"
    assert student.responses.create.await_count == 1
    assert student.responses.create.call_args.kwargs["stream"] is True
    assert student.stream.closed
    student.close.assert_awaited_once()
    repeat = await client.post(url, json=payload)
    assert repeat.headers["content-type"].startswith("application/json")
    assert repeat.json()["run_id"] == accepted["run_id"]
    assert repeat.json()["message"] == completed["message"]
    assert student.responses.create.await_count == 1
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.text.count("data-message-id=") == 2


async def test_concurrent_requests_share_db_execution_rights(
    data, client, student
):
    login(client, data.owner)
    gate = asyncio.Event()
    called = asyncio.Event()
    student.stream.events.insert(0, gate)

    async def create(**kwargs):
        called.set()
        return student.stream

    student.responses.create.side_effect = create
    payload = {"request_id": str(uuid4()), "content": "Concurrent?"}
    url = f"/sessions/{data.session.id}/turns/stream"
    first = asyncio.create_task(client.post(url, json=payload))
    try:
        await asyncio.wait_for(called.wait(), 3)
        replay, different = await asyncio.gather(
            client.post(url, json=payload),
            client.post(url, json={**payload, "request_id": str(uuid4())}),
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == "running"
        assert different.status_code == 409
        assert different.json()["detail"] == {
            "code": "student_busy",
            "run_id": replay.json()["run_id"],
        }
        changed = await client.post(url, json={**payload, "content": "Changed"})
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "request_conflict"
        # Independent SQLite writer proves the LLM wait holds no transaction.
        from sqlalchemy import text

        async with data.engine.begin() as writer:
            await writer.execute(
                text(
                    "UPDATE scenario SET title='Edited while running' "
                    "WHERE id=1"
                )
            )
        updates = await client.get(
            f"/sessions/{data.session.id}/messages/updates"
        )
        assert updates.text.count("data-message-id=") == 1
        assert "Concurrent?" in updates.text
        assert student.responses.create.await_count == 1
    finally:
        gate.set()
        await first


async def test_failed_turn_retry_preserves_teacher_and_blocks_new_question(
    data, client, student
):
    login(client, data.owner)
    student.stream.events = [
        event("response.output_text.delta", delta="Partial"),
        RuntimeError("secret provider failure"),
    ]
    url = f"/sessions/{data.session.id}/turns/stream"
    payload = {"request_id": str(uuid4()), "content": "Original question "}
    failure = await client.post(url, json=payload)
    accepted, terminal = frames(failure)[0][1], frames(failure)[-1]
    assert terminal[0] == "run.failed"
    assert terminal[1]["code"] == "invalid_output"
    assert "secret provider failure" not in failure.text
    state = await client.get(f"/runs/{accepted['run_id']}")
    assert state.json()["partial_text"] == "Partial"
    assert state.json()["message"] is None
    unresolved = await client.post(
        url,
        json={
            "request_id": str(uuid4()),
            "content": "New question",
        },
    )
    assert unresolved.status_code == 409
    assert unresolved.json()["detail"]["code"] == "unresolved_turn"
    wrong = await client.post(
        url,
        json={
            "request_id": str(uuid4()),
            "content": "Original question",
            "turn_id": accepted["turn_id"],
        },
    )
    assert wrong.status_code == 409
    student.stream = FakeStream(
        [
            event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output_text="Retry answer",
                    output=[],
                    usage=None,
                ),
            )
        ]
    )
    student.responses.create.return_value = student.stream
    retry_payload = {
        **payload,
        "request_id": str(uuid4()),
        "turn_id": accepted["turn_id"],
    }
    retry = await client.post(url, json=retry_payload)
    retried = frames(retry)[0][1]
    assert retried["teacher_message_id"] == accepted["teacher_message_id"]
    assert retried["turn_id"] == accepted["turn_id"]
    assert retried["turn_index"] == 1
    assert retried["run_id"] != accepted["run_id"]
    completed_retry = await client.post(
        url,
        json={
            **retry_payload,
            "request_id": str(uuid4()),
        },
    )
    assert completed_retry.json()["run_id"] == retried["run_id"]
    assert student.responses.create.await_count == 2
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.text.count("data-message-id=") == 2
    assert "Partial" not in updates.text
    assert (
        "Original question" in updates.text and "Retry answer" in updates.text
    )


async def test_legacy_write_cannot_bypass_execution_rights(
    data, client, student
):
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/messages", data={"content": "Old tab"}
    )
    assert response.status_code == 410
    assert response.json()["code"] == "reload_required"
    assert "새로고침" in response.json()["detail"]
    student.responses.create.assert_not_awaited()
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 204


async def test_new_student_api_requires_authentication(data, client, student):
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Unauthenticated"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    student.responses.create.assert_not_awaited()

"""Student turn contracts at authenticated HTTP + file SQLite boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture

from src.services import base

client = client_fixture
scenario_payload = scenario_fixture


def event(kind, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for item in self.events:
            if isinstance(item, Exception):
                raise item
            if isinstance(item, asyncio.Event):
                await item.wait()
            else:
                yield item

    async def close(self):
        self.closed = True


@pytest.fixture
async def student(data, scenario_payload, monkeypatch):
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
    )
    monkeypatch.setattr(base, "AsyncOpenAI", lambda **kw: fake)
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
    assert terminal[1]["code"] == "provider_error"
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

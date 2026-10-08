"""Simultaneous first POSTs on independent SQLite request connections."""

import asyncio
from uuid import uuid4

import pytest
from test_scenario_api import login
from test_student_generation import frames

pytest_plugins = ("test_student_generation",)


@pytest.mark.parametrize("same_request", [True, False])
async def test_simultaneous_first_requests_make_one_provider_call(
    data, client, student, same_request
):
    login(client, data.owner)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    payload = {"request_id": str(uuid4()), "content": "Simultaneous question"}
    second = (
        payload if same_request else {**payload, "request_id": str(uuid4())}
    )
    url = f"/sessions/{data.session.id}/turns/stream"
    tasks = [
        asyncio.create_task(client.post(url, json=body))
        for body in (payload, second)
    ]
    try:
        done, running = await asyncio.wait(
            tasks, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        assert len(done) == len(running) == 1
        other = done.pop().result()
        if same_request:
            assert other.status_code == 200
            assert other.json()["status"] == "running"
            run_id = other.json()["run_id"]
        else:
            assert other.status_code == 409
            assert other.json()["detail"]["code"] == "student_busy"
            run_id = other.json()["detail"]["run_id"]
        state = await client.get(f"/runs/{run_id}")
        assert state.json()["status"] == "running"
        updates = await client.get(
            f"/sessions/{data.session.id}/messages/updates"
        )
        assert updates.text.count("data-message-id=") == 1
        assert student.responses.create.await_count == 1
    finally:
        gate.set()
        results = await asyncio.gather(*tasks)
    stream = next(
        result
        for result in results
        if result.headers["content-type"].startswith("text/event-stream")
    )
    assert frames(stream)[-1][0] == "output.completed"
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.text.count("data-message-id=") == 2
    assert student.responses.create.await_count == 1

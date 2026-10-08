"""Real concurrent HTTP reservations and SQLite constraint/busy boundaries."""

import asyncio

import httpx2
import pytest
from sqlalchemy import event, text
from test_model_management import write
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import (
    completed,
    prepare,
    response_body,
    sdk_transport,
    sse,
)

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_concurrent_identical_requests_share_one_probe(
    data, api, monkeypatch
):
    body = await prepare(api)
    release = asyncio.Event()

    async def upstream(request, payload):
        await release.wait()
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body())
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.output_text.delta",
                delta="visible",
                sequence_number=0,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.completed",
                response=response_body("visible"),
                sequence_number=1,
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    responses = await asyncio.gather(
        *(write(api, "models/1/probes", **body) for _ in range(3))
    )
    assert [response.status_code for response in responses] == [202, 202, 202]
    assert all(response.json() == responses[0].json() for response in responses)
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 1
    release.set()
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert len(calls) == 2 and all(client.is_closed() for client in clients)


@pytest.mark.parametrize("conflicting", [False, True])
async def test_uniqueness_failure_rereads_committed_reservation(
    data, api, monkeypatch, conflicting
):
    body = await prepare(api)

    async def upstream(request, payload):
        pytest.fail(
            "Replaying a committed reservation must not create a new task"
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    injected = False

    def committed_duplicate(
        connection, cursor, statement, parameters, context, executemany
    ):
        nonlocal injected
        if not injected and statement.startswith("INSERT INTO model_probe "):
            injected = True
            if conflicting:
                cursor.execute(
                    "UPDATE model_probe SET fingerprint='other-input'"
                )
            # Leave a durable winner, then report a real SQLite uniqueness error.
            connection.connection.commit()
            cursor.execute(statement, parameters)

    event.listen(
        data.engine.sync_engine, "after_cursor_execute", committed_duplicate
    )
    try:
        result = await write(api, "models/1/probes", **body)
    finally:
        event.remove(
            data.engine.sync_engine, "after_cursor_execute", committed_duplicate
        )
    assert injected
    assert result.status_code == (409 if conflicting else 202)
    if conflicting:
        assert result.json()["detail"]["code"] == "request_conflict"
    else:
        assert (
            result.json()
            == (await api.get(f"/admin/ai/probes/{body['request_id']}")).json()
        )
    assert not clients and not calls


async def test_busy_reservation_returns_safe_storage_error_without_upstream(
    data, api, monkeypatch
):
    body = await prepare(api)

    async def upstream(request, payload):
        pytest.fail("A busy database must not call the provider")

    clients, calls = sdk_transport(monkeypatch, upstream)

    def short_busy_timeout(connection, record, proxy):
        connection.cursor().execute("PRAGMA busy_timeout=1")

    event.listen(data.engine.sync_engine, "checkout", short_busy_timeout)
    try:
        async with data.engine.connect() as lock:
            await lock.execute(text("BEGIN IMMEDIATE"))
            result = await write(api, "models/1/probes", **body)
            assert (
                result.status_code == 503
                and result.json()["code"] == "storage_unavailable"
            )
            assert "locked" not in result.text and not clients and not calls
    finally:
        event.remove(data.engine.sync_engine, "checkout", short_busy_timeout)

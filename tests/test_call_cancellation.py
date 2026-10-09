"""Cancellation, original-key completion and deadlines at the invocation seam."""

import asyncio
from contextlib import aclosing

import httpx2
import pytest
from sqlalchemy import event, text
from test_call_execution import consume, ordinary, verified_model
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import response_body, sdk_transport, sse

from src.services.invocation_types import InvocationError

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_stream_body_prevents_retry_and_generator_close_finalizes_partial_usage(
    data, api, monkeypatch
):
    from dataclasses import replace

    from src.services.call_execution import execute_call

    request = replace(
        await verified_model(data, api, monkeypatch), role="student"
    )
    from lesson_fixtures import install_snapshot

    from src.models import ModelConfig, ProviderConnection

    data.session.teacher_id = data.admin.id
    await install_snapshot(
        data,
        await data.db.get(ProviderConnection, 1),
        await data.db.get(ModelConfig, 1),
        options=request.validated_options,
    )
    closed = []

    class Partial(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield sse(
                "response.created",
                response={
                    **response_body(),
                    "status": "in_progress",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "total_tokens": 11,
                    },
                },
                sequence_number=0,
            )
            yield sse(
                "response.output_text.delta",
                delta="visible",
                sequence_number=1,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            await asyncio.sleep(30)

        async def aclose(self):
            closed.append(True)

    async def upstream(request, payload):
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Partial()
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    permit = await ordinary(data, request, operation="student")
    async with aclosing(
        execute_call(permit, request, kind="stream", session_id=data.session.id)
    ) as events:
        async for event in events:
            if event.type == "text_delta":
                break
    assert len(calls) == 1 and closed and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        row = (
            await db.execute(
                text(
                    "SELECT status,error_code,input_tokens,first_output_at,finished_at FROM api_usage_log WHERE operation='student'"
                )
            )
        ).one()
        assert row[:3] == ("cancelled", "interrupted", 10) and row[3] and row[4]
    # A closed generator returned its capacity: another ordinary call is admitted.
    (await ordinary(data, request, operation="student")).release()


async def test_ordinary_call_keeps_original_key_when_replaced_after_start(
    data, api, monkeypatch
):
    from test_provider_connections import post

    request = await verified_model(data, api, monkeypatch)
    opened, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await release.wait()
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def lesson():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(lesson())
    try:
        async with asyncio.timeout(5):
            await opened.wait()
        assert (
            await post(api, "key", 2, api_key="REPLACEMENT-KEY-SENTINEL")
        ).status_code == 200
        assert not task.done()
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await ordinary(data, request)
    finally:
        release.set()
        result = await task
    assert result[-1].type == "completed" and len(calls) == 1
    assert all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT credential_revision,status FROM api_usage_log WHERE operation='classification'"
                )
            )
        ).one() == (1, "completed")


async def test_starting_attempt_cancel_waits_for_short_commit_without_sdk_or_leak(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        pytest.fail(
            "Cancellation before the committed start must not call upstream"
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    admitted, begin = asyncio.Event(), asyncio.Event()

    async def lesson():
        permit = await ordinary(data, request)
        admitted.set()
        await begin.wait()
        return await consume(permit, request)

    starting = asyncio.Event()

    def before_insert(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith("INSERT INTO api_usage_log "):
            starting.set()

    event.listen(
        data.engine.sync_engine, "before_cursor_execute", before_insert
    )
    task = asyncio.create_task(lesson())
    await admitted.wait()
    try:
        async with data.engine.connect() as lock:
            await lock.execute(text("BEGIN IMMEDIATE"))
            begin.set()
            await starting.wait()
            task.cancel()
            await lock.rollback()
        async with asyncio.timeout(5):
            result = await task
        assert result[-1].type == "interrupted" and not clients and not calls
        async with data.engine.connect() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status,error_code,finished_at FROM api_usage_log WHERE operation='classification'"
                    )
                )
            ).one()
            assert row[:2] == ("cancelled", "interrupted") and row[2]
        (await ordinary(data, request)).release()
    finally:
        event.remove(
            data.engine.sync_engine, "before_cursor_execute", before_insert
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_virtual_total_deadline_counts_from_approval_and_uses_analysis_setting(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: original_time() + elapsed)

    async def upstream(request, payload):
        nonlocal elapsed
        elapsed = 301
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    result = await consume(await ordinary(data, request), request)
    assert result[-1].error_code == "timeout_total" and len(calls) == 1
    assert all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT status,error_code FROM api_usage_log WHERE operation='classification'"
                )
            )
        ).one() == ("timed_out", "timeout_total")


async def test_already_finalized_cancellation_stays_interrupted_and_immutable(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    opened = asyncio.Event()

    async def upstream(request, payload):
        async with data.engine.begin() as db:
            await db.execute(
                text(
                    "UPDATE api_usage_log SET status='cancelled',error_code='interrupted',finished_at='2026-10-09',usage_complete=1 WHERE operation='classification'"
                )
            )
        opened.set()
        await asyncio.sleep(30)
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def analysis():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(analysis())
    try:
        async with asyncio.timeout(5):
            await opened.wait()
        task.cancel()
        result = await task
        assert (
            result[-1].type == "interrupted"
            and result[-1].error_code == "interrupted"
        )
        assert len(calls) == 1 and all(c.is_closed() for c in clients)
        async with data.engine.connect() as db:
            assert (
                await db.execute(
                    text(
                        "SELECT status,error_code,finished_at,usage_complete FROM api_usage_log WHERE operation='classification'"
                    )
                )
            ).one() == ("cancelled", "interrupted", "2026-10-09", 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_expired_deadline_during_start_commit_blocks_sdk(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: original_time() + elapsed)

    async def upstream(request, payload):
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    admitted, begin = asyncio.Event(), asyncio.Event()

    async def analysis():
        permit = await ordinary(data, request)
        admitted.set()
        await begin.wait()
        return await consume(permit, request)

    starting = asyncio.Event()

    def before_insert(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith("INSERT INTO api_usage_log "):
            starting.set()

    event.listen(
        data.engine.sync_engine, "before_cursor_execute", before_insert
    )
    task = asyncio.create_task(analysis())
    await admitted.wait()
    try:
        async with data.engine.connect() as lock:
            await lock.execute(text("BEGIN IMMEDIATE"))
            begin.set()
            await starting.wait()
            elapsed = 301
            await lock.rollback()
        async with asyncio.timeout(5):
            result = await task
        assert (
            result[-1].error_code == "timeout_total"
            and not calls
            and not clients
        )
        async with data.engine.connect() as db:
            assert (
                await db.execute(
                    text(
                        "SELECT status,error_code FROM api_usage_log WHERE operation='classification'"
                    )
                )
            ).one() == ("timed_out", "timeout_total")
    finally:
        event.remove(
            data.engine.sync_engine, "before_cursor_execute", before_insert
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

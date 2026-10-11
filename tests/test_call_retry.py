"""Temporary failures, backoff and retry ownership at the invocation seam."""

import asyncio
from contextlib import aclosing

import httpx2
import pytest
from sqlalchemy import text
from test_call_admission import catalog_transport
from test_call_execution import consume, ordinary, verified_model
from test_model_management import write
from test_provider_connections import KEY, post
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import response_body, sdk_transport, sse

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_only_ordinary_analysis_retries_with_one_invocation_and_backoff(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        if len(calls) == 1:
            return httpx2.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"code": "rate_limit_exceeded", "message": KEY}},
            )
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    result = await consume(await ordinary(data, request), request)
    assert result[-1].type == "completed" and len(calls) == 2
    assert all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT invocation_id,attempt_no,status,retry_wait_ms FROM api_usage_log WHERE operation='analysis_unified' ORDER BY id"
                )
            )
        ).all()
        assert len(rows) == 2 and rows[0][0] == rows[1][0]
        assert rows[0][1:] == (1, "failed", None)
        assert rows[1][1:] == (2, "completed", 0)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (403, "permission"),
        (400, "invalid_output"),
        (429, "quota"),
    ],
)
async def test_analysis_does_not_retry_permanent_failures(
    data, api, monkeypatch, status, code
):
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        return httpx2.Response(
            status,
            json={
                "error": {
                    "code": (
                        "insufficient_quota" if status == 429 else "invalid"
                    ),
                    "message": KEY,
                }
            },
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    events = await consume(await ordinary(data, request), request)
    assert len(calls) == 1 and events[-1].error_code == code
    assert all(c.is_closed() for c in clients)


async def test_analysis_returns_slot_during_backoff_and_rechecks_new_limits(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: original_time() + elapsed)
    first = asyncio.Event()

    async def upstream(request, payload):
        first.set()
        return httpx2.Response(
            503, json={"error": {"code": "server_error", "message": KEY}}
        )

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def analysis():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(analysis())
    lists_opened, release = asyncio.Event(), asyncio.Event()

    async def catalog(request):
        if len(lists) == 3:
            lists_opened.set()
        await release.wait()
        return httpx2.Response(200, json={"object": "list", "data": []})

    catalog_clients, lists = catalog_transport(monkeypatch, catalog)
    catalogs = []
    try:
        await first.wait()
        # A completed failed attempt is public persisted evidence of backoff.
        async with asyncio.timeout(5):
            while True:
                async with data.engine.connect() as db:
                    status = (
                        await db.execute(
                            text(
                                "SELECT status FROM api_usage_log WHERE operation='analysis_unified'"
                            )
                        )
                    ).scalar()
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
        catalogs = [
            asyncio.create_task(
                write(api, "providers/openai/catalog", expected_version=2)
            )
            for _ in range(3)
        ]
        async with asyncio.timeout(5):
            await lists_opened.wait()
        settings = (await api.get("/admin/ai/state")).json()["settings"]
        assert (
            await write(
                api,
                "settings/update",
                expected_version=1,
                defaults={"student": None, "mentor": None, "analysis": None},
                limits={**settings["limits"], "openai": 3},
                timeouts=settings["timeouts"],
            )
        ).status_code == 200
        elapsed = 2
        async with asyncio.timeout(5):
            result = await task
        assert result[-1].error_code == "call_limit_reached" and len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(*catalogs)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert all(c.is_closed() for c in clients + catalog_clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM api_usage_log WHERE operation='analysis_unified'"
                )
            )
        ).scalar() == 1


async def test_revocation_during_backoff_interrupts_without_new_attempt(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    first = asyncio.Event()

    async def upstream(request, payload):
        first.set()
        return httpx2.Response(
            503,
            headers={"Retry-After": "20"},
            json={"error": {"code": "server_error", "message": KEY}},
        )

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def analysis():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(analysis())
    try:
        await first.wait()
        async with asyncio.timeout(5):
            while True:
                async with data.engine.connect() as db:
                    status = (
                        await db.execute(
                            text(
                                "SELECT status FROM api_usage_log WHERE operation='analysis_unified'"
                            )
                        )
                    ).scalar()
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
        assert (await post(api, "enabled", 2, enabled=False)).status_code == 200
        async with asyncio.timeout(1):
            result = await task
        assert (
            result[-1].type == "interrupted"
            and result[-1].error_code == "interrupted"
        )
        assert len(calls) == 1 and all(c.is_closed() for c in clients)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("retry_hint", ["500", "Fri, 09 Oct 2099 00:00:00 GMT"])
async def test_retry_wait_that_exceeds_deadline_never_calls_again(
    data, api, monkeypatch, retry_hint
):
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        return httpx2.Response(
            503,
            headers={"Retry-After": retry_hint},
            json={"error": {"code": "server_error", "message": KEY}},
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    result = await consume(await ordinary(data, request), request)
    assert result[-1].error_code == "transient" and len(calls) == 1
    assert all(c.is_closed() for c in clients)


async def test_only_one_retry_and_no_retry_after_received_failure_body(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    received = False

    async def upstream(request, payload):
        if received:
            body = response_body()
            body.update(
                status="failed", error={"code": "server_error", "message": KEY}
            )
            return httpx2.Response(200, json=body)
        return httpx2.Response(
            408,
            headers={"Retry-After": "0"},
            json={"error": {"code": "invalid", "message": KEY}},
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    result = await consume(await ordinary(data, request), request)
    assert result[-1].error_code == "transient" and len(calls) == 2
    received = True
    result = await consume(await ordinary(data, request), request)
    assert result[-1].error_code == "transient" and len(calls) == 3
    assert all(c.is_closed() for c in clients)


async def test_eligible_analysis_stream_failure_after_displayed_body_never_retries(
    data, api, monkeypatch
):
    from src.services.call_execution import execute_call

    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
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
                "error",
                code="server_error",
                message=KEY,
                param=None,
                sequence_number=1,
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    async with aclosing(
        execute_call(await ordinary(data, request), request, kind="stream")
    ) as events:
        result = [event async for event in events]
    assert result[-1].error_code == "transient" and len(calls) == 1
    assert all(c.is_closed() for c in clients)


async def test_http_date_retry_hint_waits_then_records_second_attempt(
    data, api, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    request = await verified_model(data, api, monkeypatch)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: original_time() + elapsed)
    hint = format_datetime(
        datetime.now(timezone.utc) + timedelta(seconds=3), usegmt=True
    )
    first = asyncio.Event()

    async def upstream(request, payload):
        if len(calls) == 1:
            first.set()
            return httpx2.Response(
                429,
                headers={"Retry-After": hint},
                json={"error": {"code": "rate_limit_exceeded", "message": KEY}},
            )
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def analysis():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(analysis())
    try:
        await first.wait()
        async with asyncio.timeout(5):
            while True:
                async with data.engine.connect() as db:
                    status = (
                        await db.execute(
                            text(
                                "SELECT status FROM api_usage_log WHERE operation='analysis_unified'"
                            )
                        )
                    ).scalar()
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
        assert not task.done() and len(calls) == 1
        elapsed = 4
        async with asyncio.timeout(5):
            result = await task
        assert result[-1].type == "completed" and len(calls) == 2
        async with data.engine.connect() as db:
            wait = (
                await db.execute(
                    text(
                        "SELECT retry_wait_ms FROM api_usage_log WHERE operation='analysis_unified' AND attempt_no=2"
                    )
                )
            ).scalar()
            assert 1000 <= wait <= 3000
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert all(c.is_closed() for c in clients)


async def test_key_replacement_during_backoff_blocks_retry_with_safe_configuration_error(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: original_time() + elapsed)

    async def upstream(request, payload):
        return httpx2.Response(
            503,
            headers={"Retry-After": "20"},
            json={"error": {"code": "server_error", "message": KEY}},
        )

    clients, calls = sdk_transport(monkeypatch, upstream)

    async def analysis():
        return await consume(await ordinary(data, request), request)

    task = asyncio.create_task(analysis())
    try:
        async with asyncio.timeout(5):
            while True:
                async with data.engine.connect() as db:
                    status = (
                        await db.execute(
                            text(
                                "SELECT status FROM api_usage_log WHERE operation='analysis_unified'"
                            )
                        )
                    ).scalar()
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
        assert (
            await post(api, "key", 2, api_key="REPLACEMENT-KEY-SENTINEL")
        ).status_code == 200
        assert not task.done()
        elapsed = 21
        async with asyncio.timeout(5):
            result = await task
        assert (
            result[-1].error_code == "configuration_unavailable"
            and len(calls) == 1
        )
        async with data.engine.connect() as db:
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM api_usage_log WHERE operation='analysis_unified'"
                    )
                )
            ).scalar() == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert all(c.is_closed() for c in clients)

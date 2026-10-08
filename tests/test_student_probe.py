"""Authenticated probe HTTP through SQLite and the pinned OpenAI SDK transport."""

import asyncio
import json
from uuid import uuid4

import httpx2
import pytest
from openai import AsyncOpenAI
from sqlalchemy import text
from test_model_management import write
from test_provider_connections import KEY, post
from test_provider_connections import api as provider_api

api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture(autouse=True)
async def cleanup_probes(api):
    yield
    from src.services.probe_execution import stop_probes

    await stop_probes()


def response_body(content="Synthetic student answer", usage=None):
    return dict(
        id="resp_probe",
        object="response",
        created_at=1,
        model="gpt-5-mini",
        status="completed",
        output=[
            dict(
                id="msg_probe",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    dict(type="output_text", text=content, annotations=[])
                ],
            )
        ],
        usage=usage,
    )


def sse(kind, **values):
    return f"event: {kind}\ndata: {json.dumps(dict(type=kind, **values))}\n\n".encode()


def sdk_transport(monkeypatch, handler, *, budget=1024, key=KEY):
    from src.services import openai_generation

    clients, calls = [], []

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {key}"
        assert request.method == "POST" and request.url.path == "/v1/responses"
        body = json.loads(request.content)
        calls.append(body)
        assert (
            body["model"] == "gpt-5-mini"
            and body["max_output_tokens"] == budget
        )
        assert body["store"] is False
        return await handler(request, body)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"].connect == 5
        sdk = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(sdk)
        return sdk

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    return clients, calls


async def prepare(api):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Probe Student",
        )
    ).status_code == 200
    return dict(expected_version=1, role="student", request_id=str(uuid4()))


async def completed(api, request_id):
    async with asyncio.timeout(5):
        while True:
            result = await api.get(f"/admin/ai/probes/{request_id}")
            assert result.status_code == 200
            if result.json()["status"] != "verifying":
                return result.json()
            await asyncio.sleep(0.01)


async def seed_finished_attempt(data, request_id):
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO api_usage_log(id,invocation_id,request_id,owner_id,status,finished_at,timestamp,usage_complete) VALUES (999,'finished-attempt',:request_id,:owner_id,'running','2026-01-01','2026-01-01',1)"
            ),
            {"request_id": request_id, "owner_id": data.admin.id},
        )


async def test_student_probe_is_durable_idempotent_and_records_two_calls(
    data, api, monkeypatch
):
    body = await prepare(api)
    release = asyncio.Event()
    usage = dict(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=dict(cached_tokens=3),
        output_tokens_details=dict(reasoning_tokens=2),
    )

    async def upstream(request, payload):
        async with data.engine.connect() as db:
            started = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM api_usage_log WHERE status='running'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert started["invocation_id"] and started["operation"] == "probe"
            assert (
                started["request_id"] == body["request_id"]
                and started["started_at"]
            )
            assert (
                started["input_tokens"] is None
                and started["finished_at"] is None
            )
        await release.wait()
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body(usage=usage))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.reasoning_text.delta",
                delta="PRIVATE-REASONING",
                sequence_number=0,
                item_id="r",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.output_text.delta",
                delta="Synthetic student answer",
                sequence_number=1,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.completed",
                response=response_body(usage=usage),
                sequence_number=2,
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    accepted = await write(api, "models/1/probes", **body)
    assert (
        accepted.status_code == 202 and accepted.json()["status"] == "verifying"
    )
    assert (
        await write(api, "models/1/probes", **body)
    ).json() == accepted.json()
    assert (
        await write(
            api, "models/1/probes", **{**body, "request_id": str(uuid4())}
        )
    ).status_code == 409
    assert (
        await write(api, "models/1/probes", **{**body, "expected_version": 2})
    ).status_code == 409
    state = (await api.get("/admin/ai/state")).json()
    assert (
        state["models"][0]["verification_state"]["student"]["status"]
        == "verifying"
    )
    assert len(calls) <= 1
    release.set()
    done = await completed(api, body["request_id"])
    assert done["status"] == "succeeded" and done["error_code"] is None
    assert (await write(api, "models/1/probes", **body)).json() == done
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    assert [bool(c.get("stream")) for c in calls] == [False, True]
    async with data.engine.connect() as db:
        rows = (
            (await db.execute(text("SELECT * FROM api_usage_log ORDER BY id")))
            .mappings()
            .all()
        )
        assert len(rows) == 2 and all(r["status"] == "completed" for r in rows)
        assert all(
            r["input_tokens"] == 10
            and r["output_tokens"] == 5
            and r["total_tokens"] == 15
            for r in rows
        )
        assert all(
            r["cache_read_tokens"] == 3
            and r["reasoning_tokens"] == 2
            and r["usage_complete"] == 1
            for r in rows
        )
        assert all(
            r["estimated_cost_usd"] is None
            and r["pricing_source"] is None
            and r["cache_write_tokens"] is None
            for r in rows
        )
        assert all(
            r["session_id"] is None
            and r["run_id"] is None
            and r["owner_id"] == data.admin.id
            for r in rows
        )
        assert [r["probe_step"] for r in rows] == ["text", "stream"]
        assert all(
            r["attempt_no"] == 1 and r["request_id"] == body["request_id"]
            for r in rows
        )
        assert len({r["invocation_id"] for r in rows}) == 2
        assert all(r["retry_wait_ms"] is None for r in rows)
        assert (
            await db.execute(text("SELECT count(*) FROM generation_run"))
        ).scalar() == 0
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert (
        state["enabled"] is False
        and state["verification_state"]["student"]["status"] == "succeeded"
    )
    dashboard = (await api.get("/admin/api-usage")).text
    assert "gpt-5-mini" in dashboard and "산정 불가" in dashboard
    assert "PRIVATE-REASONING" not in dashboard and KEY not in dashboard
    assert len(calls) == 2


async def test_probe_failure_stops_after_first_call_and_retest_clears_success(
    data, api, monkeypatch, caplog
):
    import logging

    from test_provider_connections import PASSWORD

    caplog.set_level(logging.DEBUG)
    body = await prepare(api)
    mode = "success"

    async def upstream(request, payload):
        if mode != "success":
            return httpx2.Response(
                mode,
                json={
                    "error": dict(
                        code="insufficient_quota" if mode == 429 else "bad",
                        message=KEY + PASSWORD,
                    )
                },
            )
        if payload.get("stream"):
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
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    for mode, code in [
        (401, "authentication"),
        (429, "quota"),
        (500, "transient"),
    ]:
        body["request_id"] = str(uuid4())
        before = len(calls)
        assert (await write(api, "models/1/probes", **body)).status_code == 202
        done = await completed(api, body["request_id"])
        assert done["status"] == "failed" and done["error_code"] == code
        assert len(calls) == before + 1
        assert (await api.get("/admin/ai/state")).json()["models"][0][
            "verification_state"
        ]["student"]["status"] == "failed"
    async with data.engine.connect() as db:
        rows = (
            (await db.execute(text("SELECT * FROM api_usage_log ORDER BY id")))
            .mappings()
            .all()
        )
        assert len(rows) == 5
        assert all(
            r["input_tokens"] is None
            and r["estimated_cost_usd"] is None
            and r["usage_complete"] == 0
            for r in rows
        )
    assert all(c.is_closed() for c in clients)
    assert KEY not in caplog.text and PASSWORD not in caplog.text


async def test_cancel_closes_upstream_and_keeps_probe_identity(
    data, api, monkeypatch
):
    body = await prepare(api)
    opened = asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await asyncio.sleep(30)
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await opened.wait()
    await seed_finished_attempt(data, body["request_id"])
    cancelled = await write(api, f"probes/{body['request_id']}/cancel")
    assert cancelled.status_code == 200
    done = await completed(api, body["request_id"])
    assert done["status"] == "failed" and done["error_code"] == "interrupted"
    assert all(c.is_closed() for c in clients) and len(calls) == 1
    assert (await write(api, "models/1/probes", **body)).json() == done
    async with data.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM api_usage_log WHERE id != 999")
                )
            )
            .mappings()
            .one()
        )
        assert (
            row["status"] == "cancelled"
            and row["error_code"] == "interrupted"
            and row["finished_at"]
        )
        assert row["input_tokens"] is None and row["usage_complete"] == 0
        assert (
            await db.execute(
                text(
                    "SELECT status,finished_at,usage_complete FROM api_usage_log WHERE id=999"
                )
            )
        ).one() == ("running", "2026-01-01", 1)


async def test_ledger_start_failure_blocks_provider_and_finalize_failure_is_safe(
    data, api, monkeypatch, caplog
):
    body = await prepare(api)

    async def upstream(request, payload):
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "CREATE TRIGGER block_start BEFORE INSERT ON api_usage_log BEGIN SELECT RAISE(ABORT,'PRIVATE-STORAGE-SENTINEL'); END"
            )
        )
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    done = await completed(api, body["request_id"])
    assert (
        done["status"] == "failed"
        and done["error_code"] == "configuration_unavailable"
    )
    assert calls == [] and clients == []
    assert (await write(api, "models/1/probes", **body)).json() == done
    async with data.engine.begin() as db:
        await db.execute(text("DROP TRIGGER block_start"))
        await db.execute(
            text(
                "CREATE TRIGGER block_finish BEFORE UPDATE ON api_usage_log BEGIN SELECT RAISE(ABORT,'PRIVATE-STORAGE-SENTINEL'); END"
            )
        )
    body["request_id"] = str(uuid4())
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    done = await completed(api, body["request_id"])
    assert (
        done["status"] == "failed"
        and done["error_code"] == "configuration_unavailable"
    )
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    assert "PRIVATE-STORAGE-SENTINEL" not in caplog.text
    async with data.engine.begin() as db:
        await db.execute(text("DROP TRIGGER block_finish"))


@pytest.mark.parametrize(
    "mode,code",
    [
        ("first", "timeout_first_output"),
        ("total", "timeout_total"),
        ("connect", "timeout_connect"),
    ],
)
async def test_probe_deadlines_close_sdk_without_retry(
    data, api, monkeypatch, mode, code
):
    body = await prepare(api)
    closed = []
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE app_setting SET timeouts_json=json_set(timeouts_json,'$.student_first_output',1,'$.student_total',2) WHERE id=1"
            )
        )

    class WaitingBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            kind = (
                "response.reasoning_text.delta"
                if mode == "first"
                else "response.output_text.delta"
            )
            yield sse(
                kind,
                delta="visible" if mode == "total" else "PRIVATE-REASONING",
                sequence_number=0,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            await asyncio.sleep(30)

        async def aclose(self):
            closed.append(True)

    async def upstream(request, payload):
        if mode == "connect":
            raise httpx2.ConnectTimeout(KEY, request=request)
        if payload.get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=WaitingBody(),
            )
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    done = await completed(api, body["request_id"])
    assert done["status"] == "failed" and done["error_code"] == code
    assert len(calls) == (1 if mode == "connect" else 2) and all(
        c.is_closed() for c in clients
    )
    async with data.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM api_usage_log ORDER BY id DESC LIMIT 1")
                )
            )
            .mappings()
            .one()
        )
        assert row["status"] == "timed_out" and row["error_code"] == code
        assert (row["first_output_at"] is not None) == (mode == "total")
    if mode != "connect":
        assert closed


async def test_restart_interrupts_orphan_probe_and_attempt_without_regeneration(
    data, api, monkeypatch
):
    from src.db import connection
    from src.main import app, lifespan

    body = await prepare(api)
    opened = asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await asyncio.sleep(30)
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await opened.wait()
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 200
    # Recreate the persisted pre-crash state; no live worker survives a restart.
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE model_probe SET status='verifying',error_code=NULL,finished_at=NULL"
            )
        )
        await db.execute(
            text(
                "UPDATE model_config SET verification_state=json_set(verification_state,'$.student.status','verifying','$.student.error_code',NULL,'$.student.verified_at',NULL)"
            )
        )
        await db.execute(
            text(
                "UPDATE api_usage_log SET status='running',error_code=NULL,finished_at=NULL,usage_complete=NULL"
            )
        )
    monkeypatch.setattr(connection, "AsyncSessionLocal", data.factory)
    monkeypatch.setattr(connection, "engine", data.engine)
    await seed_finished_attempt(data, body["request_id"])
    async with lifespan(app):
        done = (await api.get(f"/admin/ai/probes/{body['request_id']}")).json()
        assert (
            done["status"] == "failed" and done["error_code"] == "interrupted"
        )
        assert (await write(api, "models/1/probes", **body)).json() == done
    async with data.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM api_usage_log WHERE id != 999")
                )
            )
            .mappings()
            .one()
        )
        assert (
            row["status"] == "interrupted"
            and row["error_code"] == "interrupted"
            and row["finished_at"]
        )
        assert row["input_tokens"] is None and row["estimated_cost_usd"] is None
        assert (
            await db.execute(
                text(
                    "SELECT status,finished_at,usage_complete FROM api_usage_log WHERE id=999"
                )
            )
        ).one() == ("running", "2026-01-01", 1)
    assert len(calls) == 1 and all(c.is_closed() for c in clients)

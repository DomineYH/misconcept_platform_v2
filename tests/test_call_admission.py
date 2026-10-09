"""Concurrent HTTP/service calls with SQLite and real SDK mock transport."""

import asyncio

import httpx2
import pytest
from openai import AsyncOpenAI
from sqlalchemy import text
from test_model_management import write
from test_provider_connections import KEY, post
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes

from src.services import openai_catalog

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def catalog_transport(monkeypatch, handler):
    clients, calls = [], []

    async def upstream(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return await handler(request)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(openai_catalog, "AsyncOpenAI", factory)
    return clients, calls


@pytest.mark.parametrize("limiting", ["provider", "admin", "total"])
async def test_catalog_slots_reject_without_queue_and_lower_limits_keep_active(
    data, api, monkeypatch, limiting
):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    if limiting != "provider":
        settings = (await api.get("/admin/ai/state")).json()["settings"]
        assert (
            await write(
                api,
                "settings/update",
                expected_version=1,
                defaults={"student": None, "mentor": None, "analysis": None},
                limits={
                    **settings["limits"],
                    "openai": 10,
                    "total": 4 if limiting == "total" else 8,
                },
                timeouts=settings["timeouts"],
            )
        ).status_code == 200
    opened, release = asyncio.Event(), asyncio.Event()

    async def upstream(request):
        if len(calls) == 3:
            opened.set()
        await release.wait()
        return httpx2.Response(200, json={"object": "list", "data": []})

    clients, calls = catalog_transport(monkeypatch, upstream)
    tasks = [
        asyncio.create_task(
            write(api, "providers/openai/catalog", expected_version=2)
        )
        for _ in range(3)
    ]
    try:
        async with asyncio.timeout(5):
            await opened.wait()
        refused = await write(
            api, "providers/openai/catalog", expected_version=2
        )
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "call_limit_reached"
        assert refused.headers["Retry-After"] == "1" and len(calls) == 3
        settings = (await api.get("/admin/ai/state")).json()["settings"]
        reduced = await write(
            api,
            "settings/update",
            expected_version=settings["settings_version"],
            defaults={"student": None, "mentor": None, "analysis": None},
            limits={**settings["limits"], "openai": 2, "admin": 1},
            timeouts=settings["timeouts"],
        )
        assert reduced.status_code == 200
        assert not any(task.done() for task in tasks)
        assert (
            await write(api, "providers/openai/catalog", expected_version=2)
        ).status_code == 429
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(result.status_code == 200 for result in results)
    assert all(client.is_closed() for client in clients)
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM api_usage_log WHERE status='completed'"
                )
            )
        ).scalar() == 4


async def test_one_probe_bundle_per_admin_and_catalog_shares_slots(
    data, api, monkeypatch
):
    from uuid import uuid4

    from test_student_probe import (
        completed,
        prepare,
        sdk_transport,
    )

    body = await prepare(api)
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5.2",
            display_name="Other model",
        )
    ).status_code == 200
    opened, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await release.wait()
        return httpx2.Response(
            401, json={"error": {"code": "invalid_api_key", "message": KEY}}
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await opened.wait()
    try:
        result = await write(
            api, "models/2/probes", **{**body, "request_id": str(uuid4())}
        )
        assert (
            result.status_code == 429
            and result.json()["detail"]["code"] == "call_limit_reached"
        )
        settings = (await api.get("/admin/ai/state")).json()["settings"]
        assert (
            await write(
                api,
                "settings/update",
                expected_version=1,
                defaults={"student": None, "mentor": None, "analysis": None},
                limits={**settings["limits"], "admin": 1},
                timeouts=settings["timeouts"],
            )
        ).status_code == 200
        assert (
            await write(api, "providers/openai/catalog", expected_version=2)
        ).status_code == 429
        assert (await write(api, "models/1/probes", **body)).status_code == 202
    finally:
        release.set()
    assert (await completed(api, body["request_id"]))[
        "error_code"
    ] == "authentication"
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 1


@pytest.mark.parametrize(
    "operation,extra", [("enabled", {"enabled": False}), ("delete", {})]
)
async def test_revocation_cancels_all_active_revisions_and_releases_slots(
    data, api, monkeypatch, operation, extra
):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    opened = asyncio.Event()

    async def upstream(request):
        if len(calls) == 2:
            opened.set()
        await asyncio.sleep(30)
        return httpx2.Response(200, json={"object": "list", "data": []})

    clients, calls = catalog_transport(monkeypatch, upstream)
    tasks = [
        asyncio.create_task(
            write(api, "providers/openai/catalog", expected_version=2)
        )
    ]
    try:
        async with asyncio.timeout(5):
            while not calls:
                await asyncio.sleep(0.01)
        # Existing revision is allowed to run through a key replacement.
        assert (await post(api, "key", 2, api_key=KEY)).status_code == 200
        assert not tasks[0].done()
        tasks.append(
            asyncio.create_task(
                write(api, "providers/openai/catalog", expected_version=3)
            )
        )
        async with asyncio.timeout(5):
            await opened.wait()
        state = (await api.get("/admin/ai/state")).json()
        assert "활성 호출: 2건" in state["providers"][0]["impact"]
        assert (await post(api, operation, 3, **extra)).status_code == 200
        async with asyncio.timeout(1):
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(
            isinstance(r, asyncio.CancelledError) or r.status_code == 503
            for r in results
        )
        assert all(c.is_closed() for c in clients)
        assert (
            await write(api, "providers/openai/catalog", expected_version=4)
        ).status_code == 422
        async with data.engine.connect() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT status,error_code,finished_at FROM api_usage_log"
                    )
                )
            ).all()
            assert len(rows) == 2 and all(
                r[0:2] == ("cancelled", "interrupted") and r[2] for r in rows
            )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_concurrent_different_models_reserve_only_one_owned_bundle(
    data, api, monkeypatch
):
    import json
    from uuid import uuid4

    from test_student_probe import completed, prepare

    body = await prepare(api)
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5.2",
            display_name="Other model",
        )
    ).status_code == 200
    release = asyncio.Event()
    calls, clients = [], []

    async def upstream(request):
        calls.append(json.loads(request.content))
        await release.wait()
        return httpx2.Response(
            401, json={"error": {"code": "invalid_api_key", "message": KEY}}
        )

    def factory(**kwargs):
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(client)
        return client

    from src.services import openai_generation

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    requests = [{**body, "request_id": str(uuid4())} for _ in range(2)]
    try:
        results = await asyncio.gather(
            *(
                write(api, f"models/{i+1}/probes", **request)
                for i, request in enumerate(requests)
            )
        )
        assert sorted(r.status_code for r in results) == [202, 429]
        winner = next(
            r.json()["request_id"] for r in results if r.status_code == 202
        )
    finally:
        release.set()
    assert (await completed(api, winner))["error_code"] == "authentication"
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 1

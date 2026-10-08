"""Claude catalog at authenticated HTTP, SQLite and official SDK transport."""

import json

import httpx2
import pytest
from sqlalchemy import text
from test_model_management import write
from test_provider_connections import KEY, PASSWORD
from test_provider_connections import api as provider_api

api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
MODEL = "claude-sonnet-4-6"


async def save_key(api):
    return await write(
        api,
        "providers/anthropic/key",
        expected_version=1,
        api_key=KEY,
        current_password=PASSWORD,
    )


def sdk_transport(monkeypatch, handler, module="anthropic_catalog"):
    from importlib import import_module

    from anthropic import AsyncAnthropic

    from src import services

    adapter = import_module(f"{services.__name__}.{module}")
    clients, calls = [], []

    async def transport(request):
        assert request.headers["x-api-key"] == KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        calls.append(request)
        return await handler(request)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"].connect == 5
        client = AsyncAnthropic(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(transport)
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(adapter, "AsyncAnthropic", factory)
    return clients, calls


def model_info(model_id=MODEL):
    return dict(
        id=model_id,
        type="model",
        display_name="Claude Sonnet 4.6",
        created_at="2026-02-17T00:00:00Z",
        max_input_tokens=1000000,
        max_tokens=128000,
        capabilities={
            "structured_outputs": {"supported": True},
            "thinking": {
                "supported": True,
                "types": {
                    "adaptive": {"supported": True},
                    "enabled": {"supported": True},
                },
            },
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "medium": {"supported": True},
                "high": {"supported": True},
                "max": {"supported": True},
            },
        },
    )


async def test_claude_catalog_reads_all_pages_and_exposes_safe_metadata(
    data, api, monkeypatch
):
    initial = (await api.get("/admin/ai/state")).json()["providers"][1]
    assert initial["catalog"]["available"] is True
    assert (await save_key(api)).status_code == 200

    async def upstream(request):
        assert request.method == "GET" and request.url.path == "/v1/models"
        after = request.url.params.get("after_id")
        item = model_info("unlisted" if after else MODEL)
        item["private_body"] = KEY
        return httpx2.Response(
            200,
            json={
                "data": [item],
                "has_more": not bool(after),
                "first_id": item["id"],
                "last_id": item["id"],
            },
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await api.get("/admin/ai/state")).status_code == 200 and not calls
    refreshed = await write(
        api, "providers/anthropic/catalog", expected_version=2
    )
    assert refreshed.status_code == 200
    public = (await api.get("/admin/ai/state")).json()["providers"][1]
    assert public["catalog"]["stale"] is False and public["verified_at"]
    assert [m["model_id"] for m in public["catalog"]["models"]] == [
        MODEL,
        "unlisted",
    ]
    assert public["catalog"]["models"][0]["max_tokens"] == 128000
    assert (
        public["catalog"]["models"][0]["capabilities"]["structured_outputs"][
            "supported"
        ]
        is True
    )
    assert KEY not in json.dumps(public) and "private_body" not in json.dumps(
        public
    )
    assert len(calls) == 2 and calls[1].url.params["after_id"] == MODEL
    assert all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        row = (
            await db.execute(
                text(
                    "SELECT provider,status,total_tokens,estimated_cost_usd FROM api_usage_log"
                )
            )
        ).one()
        assert row == ("anthropic", "completed", None, None)


async def test_claude_metadata_conflict_blocks_generation_without_sdk(
    data, api, monkeypatch
):
    from uuid import uuid4

    assert (await save_key(api)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="anthropic",
            model_id=MODEL,
            display_name="Claude",
        )
    ).status_code == 200
    mode = "structured"

    async def upstream(request):
        item = model_info()
        if mode == "structured":
            item["capabilities"]["structured_outputs"]["supported"] = False
        elif mode == "max":
            item["max_tokens"] = 4096
        elif mode == "retired":
            item["lifecycle"] = "retired"
        elif mode == "thinking":
            item["capabilities"]["thinking"]["types"]["adaptive"][
                "supported"
            ] = False
        elif mode == "effort":
            item["capabilities"]["effort"]["max"]["supported"] = False
        else:
            item.pop("capabilities")
        return httpx2.Response(
            200,
            json=dict(
                data=[item], has_more=False, first_id=MODEL, last_id=MODEL
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    for mode in (
        "structured",
        "max",
        "retired",
        "thinking",
        "effort",
        "absent",
    ):
        assert (
            await write(api, "providers/anthropic/catalog", expected_version=2)
        ).status_code == 200
        model = (await api.get("/admin/ai/state")).json()["models"][0]
        assert model["capabilities"]["metadata_conflict"] is (
            mode != "absent"
        ), mode
        if mode != "absent":
            rejected = await write(
                api,
                "models/1/probes",
                expected_version=1,
                role="student",
                request_id=str(uuid4()),
            )
            assert rejected.status_code == 422
            assert (
                rejected.json()["detail"]["code"]
                == "capability_definition_required"
            )
    assert len(calls) == 6 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text("SELECT DISTINCT operation FROM api_usage_log")
            )
        ).scalars().all() == ["model_list"]


async def test_partial_catalog_failures_preserve_last_success_and_safe_errors(
    data, api, monkeypatch, caplog, recwarn
):
    assert (await save_key(api)).status_code == 200
    mode = "success"

    async def upstream(request):
        if request.url.params.get("after_id"):
            return httpx2.Response(
                403,
                json={
                    "type": "error",
                    "error": {
                        "type": "permission_error",
                        "message": "PRIVATE-BODY",
                    },
                },
            )
        item = model_info()
        if mode == "duplicate":
            values = [item, item]
        else:
            values = [item]
        if mode == "bad_metadata":
            item["capabilities"]["structured_outputs"]["supported"] = "true"
        if mode == "secret_metadata":
            item["display_name"] = KEY
        if mode == "secret_capability":
            item["capabilities"]["structured_outputs"]["supported"] = KEY
        result = dict(
            data=values,
            has_more=mode in {"permission", "missing_cursor"},
            first_id=MODEL,
        )
        if mode != "missing_cursor":
            result["last_id"] = MODEL
        return httpx2.Response(200, json=result)

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (
        await write(api, "providers/anthropic/catalog", expected_version=2)
    ).status_code == 200
    saved = (await api.get("/admin/ai/state")).json()["providers"][1]["catalog"]
    for mode in (
        "permission",
        "duplicate",
        "bad_metadata",
        "secret_metadata",
        "secret_capability",
        "missing_cursor",
    ):
        response = await write(
            api, "providers/anthropic/catalog", expected_version=2
        )
        assert response.status_code == 503
        provider = (await api.get("/admin/ai/state")).json()["providers"][1]
        assert provider["catalog"]["models"] == saved["models"]
        assert provider["catalog"]["fetched_at"] == saved["fetched_at"]
        assert provider["catalog"]["stale"] is True
        assert provider["error_code"] == (
            "permission" if mode == "permission" else "invalid_output"
        )
        assert "PRIVATE" not in json.dumps(
            provider
        ) + caplog.text and KEY not in json.dumps(provider)
    assert KEY not in str([str(w.message) for w in recwarn])
    assert len(calls) == 8 and all(c.is_closed() for c in clients)


async def test_catalog_ttl_and_late_old_key_result_keep_reference_cache(
    data, api, monkeypatch
):
    import asyncio

    assert (await save_key(api)).status_code == 200
    opened, release = asyncio.Event(), asyncio.Event()
    blocked = False

    async def upstream(request):
        if blocked:
            opened.set()
            await release.wait()
        model_id = "later-old-key" if blocked else MODEL
        return httpx2.Response(
            200,
            json=dict(
                data=[model_info(model_id)],
                has_more=False,
                first_id=model_id,
                last_id=model_id,
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (
        await write(api, "providers/anthropic/catalog", expected_version=2)
    ).status_code == 200
    saved = (await api.get("/admin/ai/state")).json()["providers"][1][
        "catalog"
    ]["models"]
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE provider_connection SET catalog_fetched_at='2020-01-01' WHERE provider='anthropic'"
            )
        )
    public = (await api.get("/admin/ai/state")).json()["providers"][1]
    assert (
        public["catalog"]["stale"] is True
        and public["catalog"]["models"] == saved
    )
    blocked = True
    task = asyncio.create_task(
        write(api, "providers/anthropic/catalog", expected_version=2)
    )
    try:
        async with asyncio.timeout(10):
            await opened.wait()
        assert (
            await write(
                api,
                "providers/anthropic/key",
                expected_version=2,
                api_key="REPLACEMENT-SENTINEL",
                current_password=PASSWORD,
            )
        ).status_code == 200
    finally:
        release.set()
    assert (await task).status_code == 409
    public = (await api.get("/admin/ai/state")).json()["providers"][1]
    assert (
        public["credential_revision"] == 2
        and public["catalog"]["stale"] is True
    )
    assert public["catalog"]["models"] == saved and public["catalog"][
        "fetched_at"
    ].startswith("2020-01-01")
    assert (
        await write(api, "providers/anthropic/catalog", expected_version=2)
    ).status_code == 409
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT credential_revision,status FROM api_usage_log ORDER BY id"
                )
            )
        ).all() == [(1, "completed"), (1, "completed")]

"""Authenticated Gemini catalog with official SDK over mock HTTPX."""

import httpx
import pytest
from google.genai import Client
from test_model_management import write
from test_provider_connections import KEY, PASSWORD
from test_provider_connections import api as provider_api

api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def save_key(api):
    response = await api.post(
        "/admin/ai/providers/google/key",
        json=dict(expected_version=1, current_password=PASSWORD, api_key=KEY),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200


def install(monkeypatch, handler):
    from src.services import google_client

    clients, calls = [], []
    original = httpx.AsyncClient

    async def upstream(request):
        assert request.headers["x-goog-api-key"] == KEY
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.extensions["timeout"] == dict(
            connect=5, read=None, write=None, pool=None
        )
        calls.append(request)
        return await handler(request)

    def transport(**kwargs):
        client = original(**kwargs, transport=httpx.MockTransport(upstream))
        clients.append(client)
        return client

    def sdk(**kwargs):
        options = kwargs["http_options"]
        assert options.retry_options.attempts == 1
        assert options.httpx_async_client is clients[-1]
        assert options.timeout is None
        assert kwargs["vertexai"] is False
        return Client(**kwargs)

    monkeypatch.setattr(google_client, "AsyncClient", transport)
    monkeypatch.setattr(google_client, "Client", sdk)
    return clients, calls


def model(name="models/gemini-2.5-flash", **extra):
    return dict(
        name=name,
        displayName="Gemini Flash",
        version="001",
        inputTokenLimit=1048576,
        outputTokenLimit=65536,
        supportedGenerationMethods=["generateContent"],
        thinking=True,
        maxTemperature=2,
        **extra,
    )


async def test_catalog_all_pages_and_failed_refresh_preserve_cache(
    data, api, monkeypatch
):
    await save_key(api)
    fail = False

    async def upstream(request):
        assert request.method == "GET" and request.url.path == "/v1beta/models"
        if request.url.params.get("pageToken"):
            if fail:
                return httpx.Response(
                    503,
                    json={"error": {"code": 503, "message": KEY + PASSWORD}},
                )
            return httpx.Response(
                200, json={"models": [model("models/custom")]}
            )
        return httpx.Response(
            200, json={"models": [model()], "nextPageToken": "next"}
        )

    clients, calls = install(monkeypatch, upstream)
    before = (await api.get("/admin/ai/state")).json()
    assert before["providers"][2]["catalog"]["available"] and not calls
    assert (
        await write(api, "providers/google/catalog", expected_version=2)
    ).status_code == 200
    catalog = (await api.get("/admin/ai/state")).json()["providers"][2][
        "catalog"
    ]
    assert [m["model_id"] for m in catalog["models"]] == [
        "custom",
        "gemini-2.5-flash",
    ]
    assert catalog["models"][1]["thinking"] is True
    assert catalog["models"][1]["max_temperature"] == 2
    assert len(calls) == 2 and clients[0].is_closed
    fail = True
    response = await write(api, "providers/google/catalog", expected_version=2)
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "transient"
    )
    after = (await api.get("/admin/ai/state")).json()["providers"][2]["catalog"]
    assert (
        after["models"] == catalog["models"]
        and after["fetched_at"] == catalog["fetched_at"]
    )
    assert (
        after["stale"] and len(calls) == 4 and all(c.is_closed for c in clients)
    )
    assert KEY not in response.text and PASSWORD not in response.text


async def test_catalog_old_revision_authentication_and_metadata_block_probes(
    data, api, monkeypatch, caplog
):
    from uuid import uuid4

    from test_provider_connections import KEY, PASSWORD

    await save_key(api)
    assert (
        await write(
            api,
            "models",
            provider="google",
            model_id="gemini-2.5-flash",
            display_name="Gemini",
        )
    ).status_code == 200
    mode = "conflict"

    async def upstream(request):
        if mode == "auth":
            return httpx.Response(
                403, json={"error": {"code": 403, "message": KEY + PASSWORD}}
            )
        if mode == "race":
            changed = await api.post(
                "/admin/ai/providers/google/key",
                json=dict(
                    expected_version=2, current_password=PASSWORD, api_key=KEY
                ),
                headers={"x-csrf-token": api.cookies["csrftoken"]},
            )
            assert changed.status_code == 200
            return httpx.Response(
                200, json={"models": [model("models/late-old-key")]}
            )
        item = model()
        item["maxTemperature"] = 1
        return httpx.Response(200, json={"models": [item]})

    clients, calls = install(monkeypatch, upstream)
    assert (
        await write(api, "providers/google/catalog", expected_version=2)
    ).status_code == 200
    before = (await api.get("/admin/ai/state")).json()
    assert before["models"][0]["capabilities"]["metadata_conflict"]
    refused = await write(
        api,
        "models/1/probes",
        expected_version=1,
        role="student",
        request_id=str(uuid4()),
    )
    assert refused.status_code == 422 and len(calls) == 1
    mode = "auth"
    failed = await write(api, "providers/google/catalog", expected_version=2)
    assert (
        failed.status_code == 503
        and failed.json()["detail"]["code"] == "permission"
    )
    mode = "race"
    assert (
        await write(api, "providers/google/catalog", expected_version=2)
    ).status_code == 409
    after = (await api.get("/admin/ai/state")).json()
    assert (
        after["providers"][2]["catalog"]["models"]
        == before["providers"][2]["catalog"]["models"]
    )
    assert after["providers"][2]["catalog"]["stale"]
    assert all(c.is_closed for c in clients) and len(calls) == 3
    assert (
        KEY not in failed.text + caplog.text
        and PASSWORD not in failed.text + caplog.text
    )


async def test_google_registration_rejects_empty_resource_id_and_invalid_options(
    api,
):
    await save_key(api)
    empty = await write(
        api,
        "models",
        provider="google",
        model_id="models/",
        display_name="Empty",
    )
    assert empty.status_code == 422
    assert (
        await write(
            api,
            "models",
            provider="google",
            model_id="models/gemini-2.5-flash",
            display_name="Flash",
        )
    ).status_code == 200
    duplicate = await write(
        api,
        "models",
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Duplicate",
    )
    assert duplicate.status_code == 409
    for options in [
        {"thinking": {"budget": 0, "level": "low"}},
        {"thinking": {"level": "low"}},
        {"temperature": 3},
    ]:
        result = await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Flash",
            enabled=False,
            default_options=options,
        )
        assert result.status_code == 422

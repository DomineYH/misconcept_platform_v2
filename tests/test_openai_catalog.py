"""DB credentials through real HTTP, SQLite, and the pinned SDK transport."""

import httpx2
import pytest
from openai import AsyncOpenAI
from test_model_management import write
from test_provider_connections import KEY, PASSWORD, post
from test_provider_connections import api as provider_api

api = provider_api

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def sdk_transport(monkeypatch, handler):
    from src.services import openai_catalog

    clients, requests = [], []

    async def upstream(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return await handler(request)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"].connect == 5
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(openai_catalog, "AsyncOpenAI", factory)
    return clients, requests


def model_list(*ids):
    return {
        "object": "list",
        "data": [
            dict(
                id=id,
                object="model",
                created=1,
                owned_by="openai",
                shutdown_date=None,
            )
            for id in ids
        ],
    }


async def test_manual_catalog_and_cached_reads_never_generate(
    data, api, monkeypatch
):
    initial = (await api.get("/admin/ai/state")).json()
    assert initial["providers"][0]["catalog"]["available"] is True
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200

    async def handler(request):
        return httpx2.Response(
            200, json=model_list("gpt-5.2", "unlisted-custom", "gpt-5-mini")
        )

    clients, requests = sdk_transport(monkeypatch, handler)
    assert (
        await api.get("/admin/ai/state")
    ).status_code == 200 and requests == []
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    provider = (await api.get("/admin/ai/state")).json()["providers"][0]
    assert [m["model_id"] for m in provider["catalog"]["models"]] == [
        "gpt-5-mini",
        "gpt-5.2",
        "unlisted-custom",
    ]
    assert (
        provider["catalog"]["stale"] is False
        and provider["catalog"]["fetched_at"]
    )
    assert provider["verified_at"] and provider["error_code"] is None
    assert (
        provider["connection_version"] == 2
        and provider["credential_revision"] == 1
    )
    assert provider["catalog"]["models"][0]["created"] == 1
    assert len(requests) == 1 and clients[0].is_closed()
    assert (await api.get("/admin/ai/state")).json()["models"] == []
    assert len(requests) == 1
    assert (
        await write(api, "providers/openai/catalog", expected_version=1)
    ).status_code == 409
    assert len(requests) == 1
    assert KEY not in str(provider) and PASSWORD not in str(provider)


async def test_cache_failure_expiry_and_old_key_results_preserve_data(
    data, api, monkeypatch, caplog
):
    import logging

    from sqlalchemy import text

    caplog.set_level(logging.DEBUG)
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Student",
        )
    ).status_code == 200
    mode = "success"

    async def handler(request):
        if mode == "failure":

            class BrokenBody(httpx2.AsyncByteStream):
                async def __aiter__(self):
                    yield b'{"object":"list","data":['
                    raise httpx2.ReadError(KEY + PASSWORD, request=request)

            return httpx2.Response(200, stream=BrokenBody())
        if mode == "race":
            assert (await post(api, "key", 2, api_key=KEY)).status_code == 200
            return httpx2.Response(200, json=model_list("late-old-revision"))
        if mode == "missing":
            return httpx2.Response(200, json=model_list("unlisted-custom"))
        return httpx2.Response(200, json=model_list("gpt-5-mini"))

    clients, requests = sdk_transport(monkeypatch, handler)
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    before = (await api.get("/admin/ai/state")).json()["providers"][0][
        "catalog"
    ]
    mode = "failure"
    failed = await write(api, "providers/openai/catalog", expected_version=2)
    assert (
        failed.status_code == 503
        and failed.json()["detail"]["code"] == "transient"
    )
    state = (await api.get("/admin/ai/state")).json()
    after = state["providers"][0]["catalog"]
    assert (
        after["models"] == before["models"]
        and after["fetched_at"] == before["fetched_at"]
    )
    assert (
        after["stale"] is True
        and state["providers"][0]["error_code"] == "transient"
    )
    assert (
        len(state["models"]) == 1
        and state["models"][0]["verification_state"]["student"]["status"]
        == "unverified"
    )
    async with data.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE provider_connection SET catalog_fetched_at='2026-01-01', error_code=NULL WHERE id=1"
            )
        )
    assert (await api.get("/admin/ai/state")).json()["providers"][0]["catalog"][
        "stale"
    ] is True
    mode = "missing"
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    before = state["providers"][0]["catalog"]
    assert before["models"][0]["model_id"] == "unlisted-custom"
    assert len(state["models"]) == 1
    assert state["models"][0]["model_id"] == "gpt-5-mini"
    assert state["models"][0]["capabilities"] is not None
    assert (
        state["models"][0]["verification_state"]["student"]["status"]
        == "unverified"
    )
    mode = "race"
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 409
    state = (await api.get("/admin/ai/state")).json()
    assert state["providers"][0]["catalog"]["models"] == before["models"]
    assert state["providers"][0]["catalog"]["stale"] is True
    assert len(state["models"]) == 1
    assert len(requests) == 4 and all(c.is_closed() for c in clients)
    assert KEY not in caplog.text and PASSWORD not in caplog.text


async def test_catalog_errors_timeouts_and_trust_checks_never_retry(
    data, api, monkeypatch, caplog
):
    import asyncio
    import logging

    from pydantic import SecretStr
    from sqlalchemy import text
    from test_scenario_api import login

    from src.config import config

    caplog.set_level(logging.DEBUG)
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    mode = 401

    async def handler(request):
        if mode == "connect":
            raise httpx2.ConnectTimeout(KEY + PASSWORD, request=request)
        if mode == "deadline":
            await asyncio.sleep(10)
        if mode == "malformed":
            body = model_list("gpt-5-mini", "bad\x00id")
            return httpx2.Response(200, json=body)
        if mode == "envelope":
            return httpx2.Response(200, json={"object": "error", "data": None})
        return httpx2.Response(
            mode,
            json={
                "error": dict(
                    message=KEY + PASSWORD,
                    code="insufficient_quota" if mode == 429 else "bad",
                )
            },
        )

    clients, requests = sdk_transport(monkeypatch, handler)
    for mode, code in [
        (401, "authentication"),
        (403, "permission"),
        (429, "quota"),
        (500, "transient"),
        (400, "invalid_output"),
        ("connect", "timeout_connect"),
        ("malformed", "invalid_output"),
        ("envelope", "invalid_output"),
    ]:
        failed = await write(
            api, "providers/openai/catalog", expected_version=2
        )
        assert failed.status_code == 503 and failed.json() == {
            "detail": {"code": code}
        }
        assert KEY not in failed.text and PASSWORD not in failed.text
    async with data.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE app_setting SET timeouts_json=json_set(timeouts_json,'$.model_list_total',1) WHERE id=1"
            )
        )
    mode = "deadline"
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).json() == {"detail": {"code": "timeout_total"}}
    assert len(requests) == 9 and all(c.is_closed() for c in clients)
    assert KEY not in caplog.text and PASSWORD not in caplog.text
    login(api, data.owner)
    await api.get("/admin/ai")
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 403
    login(api, data.admin)
    await api.get("/admin/ai")
    assert (
        await api.post(
            "/admin/ai/providers/openai/catalog", json=dict(expected_version=2)
        )
    ).status_code == 403
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(""))
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 503
    assert (await post(api, "enabled", 2, enabled=False)).status_code == 200
    assert (
        await write(api, "providers/openai/catalog", expected_version=3)
    ).status_code == 422
    assert (
        await write(api, "providers/google/catalog", expected_version=1)
    ).status_code == 503
    assert len(requests) == 9


async def test_explicit_model_shutdown_metadata_blocks_activation(
    data, api, monkeypatch
):
    from src.models.model_config import ModelConfig

    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Student",
        )
    ).status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    async with data.factory() as db:
        stored = await db.get(ModelConfig, 1)
        stored.verification_state = {
            "student": dict(
                status="succeeded",
                credential_revision=1,
                connection_version=2,
                capability_definition_version=model["capabilities"][
                    "definition_version"
                ],
                role_contract_version="s1-v1",
            ),
            "mentor": {"status": "unverified"},
            "analysis": {"status": "unverified"},
        }
        await db.commit()

    async def handler(request):
        result = model_list("gpt-5-mini")
        result["data"][0]["shutdown_date"] = "2026-01-01"
        return httpx2.Response(200, json=result)

    sdk_transport(monkeypatch, handler)
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    public = (await api.get("/admin/ai/state")).json()["models"][0]
    assert public["capabilities"]["metadata_conflict"] is True
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Student",
            enabled=True,
            default_options={},
        )
    ).status_code == 422


async def test_catalog_attempt_is_committed_before_upstream_and_unknown_cost_stays_null(
    data, api, monkeypatch, caplog
):
    from sqlalchemy import text

    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200

    async def handler(request):
        async with data.engine.connect() as db:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM api_usage_log WHERE operation='model_list' AND status='running'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert (
                row["status"] == "running"
                and row["invocation_id"]
                and row["attempt_no"] == 1
            )
            assert (
                row["session_id"] is None
                and row["model"] is None
                and row["input_tokens"] is None
            )
        return httpx2.Response(200, json=model_list("gpt-5-mini"))

    clients, requests = sdk_transport(monkeypatch, handler)
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200
    async with data.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM api_usage_log WHERE operation='model_list'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["status"] == "completed" and row["finished_at"]
        assert row["retry_wait_ms"] is None
        assert (
            row["total_tokens"] is None
            and row["estimated_cost_usd"] is None
            and row["usage_complete"] == 0
        )
        await db.execute(
            text(
                "CREATE TRIGGER reject_list_attempt BEFORE INSERT ON api_usage_log BEGIN SELECT RAISE(ABORT,'PRIVATE-ERROR'); END"
            )
        )
        await db.commit()
    response = await write(api, "providers/openai/catalog", expected_version=2)
    assert response.status_code == 503 and "PRIVATE-ERROR" not in response.text
    assert len(requests) == 1 and all(c.is_closed() for c in clients)
    async with data.engine.begin() as db:
        await db.execute(text("DROP TRIGGER reject_list_attempt"))
        await db.execute(
            text(
                "CREATE TRIGGER reject_list_finish BEFORE UPDATE ON api_usage_log BEGIN SELECT RAISE(ABORT,'PRIVATE-ERROR'); END"
            )
        )
    response = await write(api, "providers/openai/catalog", expected_version=2)
    assert response.status_code == 503 and "PRIVATE-ERROR" not in response.text
    assert len(requests) == 2 and all(c.is_closed() for c in clients)
    assert (
        "Invocation finalization failed" in caplog.text
        and "PRIVATE-ERROR" not in caplog.text
    )

"""Common execution policy through SQLite and the official SDK transport."""

import asyncio
from contextlib import aclosing

import httpx2
import pytest
from sqlalchemy import text
from test_call_admission import catalog_transport
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

from src.services import call_admission
from src.services.invocation_types import InvocationError, TextRequest

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def verified_model(data, api, monkeypatch):
    body = await prepare(api)

    async def upstream(request, payload):
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

    sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name=state["display_name"],
            enabled=True,
            default_options={},
        )
    ).status_code == 200
    # Give the mock ordinary analysis call the same approved role contract.
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE model_config SET verification_state=json_set(verification_state,'$.analysis',json_extract(verification_state,'$.student'))"
            )
        )
    return TextRequest(
        "openai",
        "gpt-5-mini",
        "analysis",
        "Synthetic",
        [{"role": "user", "content": "Synthetic"}],
        {"max_output_tokens": 1024},
        body["request_id"],
    )


async def ordinary(data, request, *, operation="classification"):
    return await call_admission.admit_call(
        data.factory,
        connection_id=1,
        owner_id=data.admin.id,
        operation=operation,
        role=request.role,
        admin=False,
        model_config_id=1,
        expected_model_version=2,
    )


async def consume(permit, request):
    from src.services.call_execution import execute_call

    async with aclosing(execute_call(permit, request)) as events:
        return [event async for event in events]


@pytest.mark.parametrize("limiting", ["provider", "total"])
async def test_admin_capacity_preserves_one_ordinary_provider_slot(
    data, api, monkeypatch, limiting
):
    request = await verified_model(data, api, monkeypatch)
    if limiting == "total":
        settings = (await api.get("/admin/ai/state")).json()["settings"]
        assert (
            await write(
                api,
                "settings/update",
                expected_version=1,
                defaults={"student": None, "mentor": None, "analysis": None},
                limits={**settings["limits"], "total": 4, "openai": 10},
                timeouts=settings["timeouts"],
            )
        ).status_code == 200
    opened, release = asyncio.Event(), asyncio.Event()

    async def catalog(request):
        if len(lists) == 3:
            opened.set()
        await release.wait()
        return httpx2.Response(200, json={"object": "list", "data": []})

    catalog_clients, lists = catalog_transport(monkeypatch, catalog)

    async def generation(request, payload):
        await release.wait()
        return httpx2.Response(200, json=response_body())

    generation_clients, calls = sdk_transport(monkeypatch, generation)
    tasks = [
        asyncio.create_task(
            write(api, "providers/openai/catalog", expected_version=2)
        )
        for _ in range(3)
    ]

    async def lesson():
        return await consume(await ordinary(data, request), request)

    try:
        async with asyncio.timeout(5):
            await opened.wait()
        assert (
            await write(api, "providers/openai/catalog", expected_version=2)
        ).status_code == 429
        tasks.append(asyncio.create_task(lesson()))
        async with asyncio.timeout(5):
            while not calls:
                await asyncio.sleep(0.01)
        with pytest.raises(InvocationError, match="call_limit_reached"):
            await ordinary(data, request)
        # The provider wait holds no SQLite write transaction.
        async with data.engine.begin() as db:
            await db.execute(
                text("UPDATE app_setting SET updated_at='2026-10-09'")
            )
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results[:3]] == [200, 200, 200]
    assert results[3][-1].type == "completed"
    assert all(c.is_closed() for c in catalog_clients + generation_clients)


async def test_mismatched_request_is_rejected_before_sdk_or_cost_ledger(
    data, api, monkeypatch
):
    from dataclasses import replace

    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        pytest.fail("Unverified requested model must not execute")

    clients, calls = sdk_transport(monkeypatch, upstream)
    result = await consume(
        await ordinary(data, request), replace(request, model_id="gpt-5.2")
    )
    assert (
        result[-1].error_code == "configuration_unavailable"
        and not clients
        and not calls
    )
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM api_usage_log WHERE operation='classification'"
                )
            )
        ).scalar() == 0
    (await ordinary(data, request)).release()


async def test_model_version_is_rechecked_after_slot_approval(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        pytest.fail("Changed model must not execute an admitted request")

    clients, calls = sdk_transport(monkeypatch, upstream)
    permit = await ordinary(data, request)
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=2,
            display_name=model["display_name"],
            enabled=False,
            default_options={},
        )
    ).status_code == 200
    result = await consume(permit, request)
    assert (
        result[-1].error_code == "configuration_unavailable"
        and not clients
        and not calls
    )
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT status,error_code FROM api_usage_log WHERE operation='classification'"
                )
            )
        ).one() == ("failed", "configuration_unavailable")


async def test_catalog_conflict_after_activation_blocks_admission_and_recheck(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    permit = await ordinary(data, request)

    async def catalog(request):
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "gpt-5-mini",
                        "object": "model",
                        "created": 1,
                        "owned_by": "openai",
                        "shutdown_date": "2020-01-01",
                    }
                ],
            },
        )

    catalog_clients, lists = catalog_transport(monkeypatch, catalog)
    assert (
        await write(api, "providers/openai/catalog", expected_version=2)
    ).status_code == 200

    async def upstream(request, payload):
        pytest.fail("Conflicting catalog metadata must block generation")

    clients, calls = sdk_transport(monkeypatch, upstream)
    try:
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await ordinary(data, request)
        result = await consume(permit, request)
        assert result[-1].error_code == "configuration_unavailable"
        assert not clients and not calls
        assert len(lists) == 1 and all(c.is_closed() for c in catalog_clients)
        async with data.engine.connect() as db:
            assert (
                await db.execute(
                    text(
                        "SELECT status,error_code FROM api_usage_log WHERE operation='classification'"
                    )
                )
            ).one() == ("failed", "configuration_unavailable")
    finally:
        permit.release()


async def test_cancel_successful_probe_is_idempotent_noop(
    data, api, monkeypatch
):
    request = await verified_model(data, api, monkeypatch)
    before = (await api.get(f"/admin/ai/probes/{request.request_id}")).json()

    async def upstream(request, payload):
        pytest.fail("Completed cancellation cannot call the provider")

    clients, calls = sdk_transport(monkeypatch, upstream)
    for _ in range(2):
        response = await write(api, f"probes/{request.request_id}/cancel")
        assert response.status_code == 200 and response.json() == {
            "status": "cancel_requested"
        }
    assert (
        await api.get(f"/admin/ai/probes/{request.request_id}")
    ).json() == before
    assert not clients and not calls
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM api_usage_log WHERE status='completed'"
                )
            )
        ).scalar() == 2


async def test_unknown_provider_never_sends_its_key_to_openai(
    data, api, monkeypatch
):
    from test_call_admission import catalog_transport
    from test_provider_connections import KEY, PASSWORD

    from src.services.call_execution import execute_call

    response = await api.post(
        "/admin/ai/providers/anthropic/key",
        json={
            "expected_version": 1,
            "current_password": PASSWORD,
            "api_key": KEY,
        },
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200

    async def upstream(request):
        return httpx2.Response(200, json={"object": "list", "data": []})

    clients, calls = catalog_transport(monkeypatch, upstream)
    permit = await call_admission.admit_call(
        data.factory,
        connection_id=2,
        owner_id=data.admin.id,
        operation="model_list",
    )
    permit.provider = "unsupported"
    async with aclosing(execute_call(permit, kind="catalog")) as events:
        result = [event async for event in events]
    assert (
        result[-1].error_code == "configuration_unavailable"
        and not clients
        and not calls
    )
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM api_usage_log"))
        ).scalar() == 0

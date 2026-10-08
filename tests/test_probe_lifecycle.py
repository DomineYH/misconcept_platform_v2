"""Probe ownership, version fencing and call-start settings at the HTTP seam."""

import asyncio

import httpx2
import pytest
from sqlalchemy import text
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


async def test_later_probe_call_uses_new_timeouts_without_changing_active_call(
    data, api, monkeypatch
):
    body = await prepare(api)
    observed = []

    async def upstream(request, payload):
        observed.append(clients[-1].timeout.read)
        if not payload.get("stream"):
            async with data.engine.begin() as db:
                await db.execute(
                    text(
                        "UPDATE app_setting SET timeouts_json=json_set(timeouts_json,'$.student_first_output',1,'$.student_total',2)"
                    )
                )
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
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert observed == [180, 2] and len(calls) == 2


async def test_invalid_stored_options_keep_management_readable_and_reject_probe(
    data, api, monkeypatch
):
    body = await prepare(api)

    async def upstream(request, payload):
        pytest.fail("Invalid stored options must never call OpenAI")

    clients, calls = sdk_transport(monkeypatch, upstream)
    async with data.engine.begin() as db:
        await db.execute(
            text("UPDATE model_config SET default_options_json=:options"),
            {"options": '{"temperature":1}'},
        )
    state = await api.get("/admin/ai/state")
    assert state.status_code == 200
    assert state.json()["models"][0]["probe_budgets"] == {}
    rejected = await write(api, "models/1/probes", **body)
    assert (
        rejected.status_code == 422
        and rejected.json()["detail"]["code"] == "invalid_options"
    )
    assert not clients and not calls
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 0


@pytest.mark.parametrize(
    "changed", ["credential", "model", "definition", "contract", "newer_role"]
)
async def test_late_probe_cannot_verify_changed_versions_or_overwrite_newer_role(
    data, api, monkeypatch, changed
):
    from src.services.model_verification import ROLE_CONTRACT_VERSIONS

    body = await prepare(api)

    async def upstream(request, payload):
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body())
        async with data.engine.begin() as db:
            if changed == "credential":
                await db.execute(
                    text(
                        "UPDATE provider_connection SET credential_revision=credential_revision+1"
                    )
                )
            elif changed == "model":
                await db.execute(
                    text(
                        "UPDATE model_config SET config_version=config_version+1"
                    )
                )
            elif changed == "definition":
                await db.execute(
                    text(
                        "UPDATE model_config SET capability_definition_version='new-definition'"
                    )
                )
            elif changed == "newer_role":
                await db.execute(
                    text(
                        "UPDATE model_config SET config_version=config_version+1, verification_state=json_set(verification_state,'$.student',json(:state),'$.mentor',json(:state))"
                    ),
                    {"state": '{"status":"unverified"}'},
                )
            else:
                monkeypatch.setitem(
                    ROLE_CONTRACT_VERSIONS, "student", "new-contract"
                )
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
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "stale"
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert state["verification_state"]["student"]["status"] == (
        "unverified" if changed == "newer_role" else "stale"
    )
    assert state["verification_state"]["mentor"]["status"] == "unverified"
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM api_usage_log WHERE status='completed'"
                )
            )
        ).scalar() == 2


async def test_deleting_probe_owner_preserves_durable_identity(
    data, api, monkeypatch
):
    from sqlalchemy.exc import IntegrityError
    from test_scenario_api import login

    body = await prepare(api)

    async def upstream(request, payload):
        return httpx2.Response(401, json={"error": {"code": "invalid_api_key"}})

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "failed"
    data.other.role = "admin"
    await data.db.commit()
    login(api, data.other)
    await api.get("/admin/ai")
    deletion = await api.post(
        f"/admin/users/{data.admin.id}/delete",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert deletion.status_code == 400
    assert "모델 시험 기록" in deletion.text
    for statement in (
        "UPDATE model_probe SET owner_id=NULL",
        "DELETE FROM user WHERE id=:owner",
    ):
        with pytest.raises(IntegrityError):
            async with data.engine.begin() as db:
                await db.execute(text(statement), {"owner": data.admin.id})
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT owner_id FROM model_probe"))
        ).scalar() == data.admin.id
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    assert (
        await api.post(
            f"/admin/users/{data.owner.id}/delete",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    ).status_code == 200


async def test_probe_request_rejections_are_unbound_and_owner_status_is_private(
    data, api, monkeypatch
):
    from test_provider_connections import KEY
    from test_scenario_api import login

    body = await prepare(api)
    opened = asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await asyncio.sleep(30)
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/999/probes", **body)).status_code == 404
    assert (
        await write(api, "models/1/probes", **{**body, "api_key": KEY})
    ).status_code == 422
    assert (
        await write(api, "models/1/probes", **{**body, "role": "mentor"})
    ).status_code == 422
    assert (
        await api.post("/admin/ai/models/1/probes", json=body)
    ).status_code == 403
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await opened.wait()
    own = await api.get(f"/admin/ai/probes/{body['request_id']}")
    assert own.headers.get("cache-control") == "no-store"
    assert own.status_code == 200 and set(own.json()) == {
        "request_id",
        "status",
        "error_code",
    }
    assert KEY not in own.text
    data.other.role = "admin"
    await data.db.commit()
    login(api, data.other)
    await api.get("/admin/ai")
    assert (
        await api.get(f"/admin/ai/probes/{body['request_id']}")
    ).status_code == 404
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 404
    assert (await write(api, "models/1/probes", **body)).status_code == 409
    login(api, data.owner)
    await api.get("/admin/ai")
    assert (await write(api, "models/1/probes", **body)).status_code == 403
    assert (
        await api.get(f"/admin/ai/probes/{body['request_id']}")
    ).status_code == 403
    api.cookies.clear()
    assert (
        await api.get(f"/admin/ai/probes/{body['request_id']}")
    ).status_code == 401
    login(api, data.admin)
    await api.get("/admin/ai")
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 200
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 1
    login(api, data.other)
    await api.get("/admin/ai")
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        while len(calls) < 2:
            await asyncio.sleep(0.01)
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 200
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM model_probe"))
        ).scalar() == 2


@pytest.mark.parametrize("mode", ["cancel", "eof"])
async def test_partial_stream_usage_survives_cancel_or_eof_without_success(
    data, api, monkeypatch, mode
):
    body = await prepare(api)
    observed, closed = asyncio.Event(), []
    usage = dict(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=dict(cached_tokens=3),
        output_tokens_details=dict(reasoning_tokens=2),
        private="PRIVATE-USAGE",
    )

    class PartialBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield sse(
                "response.in_progress",
                response=response_body(usage=usage),
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
            observed.set()
            if mode == "cancel":
                await asyncio.sleep(30)

        async def aclose(self):
            closed.append(True)

    async def upstream(request, payload):
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body())
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=PartialBody(),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await observed.wait()
    if mode == "cancel":
        assert (
            await write(api, f"probes/{body['request_id']}/cancel")
        ).status_code == 200
    done = await completed(api, body["request_id"])
    assert done["status"] == "failed" and done["error_code"] == (
        "interrupted" if mode == "cancel" else "invalid_output"
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
        assert (
            row["input_tokens"],
            row["output_tokens"],
            row["total_tokens"],
        ) == (10, 5, 15)
        assert (
            row["cache_read_tokens"] == 3
            and row["reasoning_tokens"] == 2
            and row["usage_complete"] == 1
        )
        assert (
            row["first_output_at"]
            and row["finished_at"]
            and row["estimated_cost_usd"] is None
        )
        assert "PRIVATE" not in row["raw_usage_json"]
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 200
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text(
                    "SELECT finished_at FROM api_usage_log ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar() == row["finished_at"]
    assert closed and len(calls) == 2 and all(c.is_closed() for c in clients)


async def test_probe_preserves_validated_options_and_uses_lower_stored_budget(
    data, api, monkeypatch
):
    body = await prepare(api)
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Student",
            enabled=False,
            default_options={
                "max_output_tokens": 100,
                "reasoning": {"effort": "high"},
            },
        )
    ).status_code == 200
    body["expected_version"] = 2
    assert (await api.get("/admin/ai/state")).json()["models"][0][
        "probe_budgets"
    ] == {"student": 100}

    async def upstream(request, payload):
        assert (
            payload["reasoning"] == {"effort": "high"}
            and "temperature" not in payload
        )
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

    clients, calls = sdk_transport(monkeypatch, upstream, budget=100)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert len(calls) == 2 and all(c.is_closed() for c in clients)

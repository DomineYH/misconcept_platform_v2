"""Claude probes reuse ownership, admission, cancellation and revision fencing."""

import asyncio
import json
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import text
from test_anthropic_catalog import sdk_transport
from test_anthropic_probes import prepare, response_body, stream_body
from test_model_management import write
from test_provider_connections import KEY, PASSWORD
from test_provider_connections import api as provider_api
from test_scenario_api import login
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import completed, sse

api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize(
    "mode", ["cancel", "credential", "model", "disable", "delete"]
)
async def test_claude_active_probe_is_owned_cancellable_and_version_fenced(
    data, api, monkeypatch, mode
):
    await prepare(api)
    observed, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Partial(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield stream_body(finish=False)
            observed.set()
            await release.wait()
            yield sse("message_stop")

        async def aclose(self):
            closed.append(True)

    async def upstream(request):
        if not json.loads(request.content).get("stream"):
            return httpx2.Response(200, json=response_body())
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Partial()
        )

    clients, calls = sdk_transport(
        monkeypatch, upstream, "anthropic_generation"
    )
    body = dict(expected_version=1, role="student", request_id=str(uuid4()))
    assert (
        await write(api, "models/1/probes", **{**body, "expected_version": 2})
    ).status_code == 409
    assert (
        await api.post("/admin/ai/models/1/probes", json=body)
    ).status_code == 403
    assert not clients
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    try:
        async with asyncio.timeout(10):
            await observed.wait()
        assert (await write(api, "models/1/probes", **body)).status_code == 202
        assert (
            await write(api, "models/1/probes", **{**body, "role": "mentor"})
        ).status_code == 409
        assert (
            await write(
                api, "models/1/probes", **{**body, "request_id": str(uuid4())}
            )
        ).status_code == 409
        assert (
            await write(
                api,
                "models/1/probes",
                **{**body, "role": "mentor", "request_id": str(uuid4())},
            )
        ).status_code == 429
        if mode == "cancel":
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
            login(api, data.owner)
            await api.get("/admin/ai")
            assert (
                await write(api, "models/1/probes", **body)
            ).status_code == 403
            login(api, data.admin)
            await api.get("/admin/ai")
            assert (
                await write(api, f"probes/{body['request_id']}/cancel")
            ).status_code == 200
        elif mode == "credential":
            assert (
                await write(
                    api,
                    "providers/anthropic/key",
                    expected_version=2,
                    api_key="REPLACEMENT-KEY-SENTINEL",
                    current_password=PASSWORD,
                )
            ).status_code == 200
        elif mode == "model":
            assert (
                await write(
                    api,
                    "models/1/update",
                    expected_version=1,
                    display_name="Changed",
                    enabled=False,
                    default_options={},
                )
            ).status_code == 200
        else:
            assert (
                await write(
                    api,
                    "providers/anthropic/"
                    + ("enabled" if mode == "disable" else "delete"),
                    expected_version=2,
                    current_password=PASSWORD,
                    **({"enabled": False} if mode == "disable" else {}),
                )
            ).status_code == 200
    finally:
        release.set()
    result = await completed(api, body["request_id"])
    assert result["status"] == ("failed" if mode == "cancel" else "stale")
    state = (await api.get("/admin/ai/state")).json()
    assert (
        state["models"][0]["verification_state"]["student"]["status"]
        != "succeeded"
    )
    assert KEY not in json.dumps(state) and "PRIVATE" not in json.dumps(state)
    assert len(calls) == 2 and closed and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        row = (
            await db.execute(
                text(
                    "SELECT status,error_code,credential_revision,input_tokens,output_tokens,first_output_at,finished_at FROM api_usage_log ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        assert row[:5] == (
            ("completed", None, 1, 15, 12)
            if mode in {"credential", "model"}
            else ("cancelled", "interrupted", 1, 15, 12)
        )
        assert row[5] and row[6]
    if mode in {"disable", "delete"}:
        assert (
            await write(
                api, "models/1/probes", **{**body, "request_id": str(uuid4())}
            )
        ).status_code == 503
        assert len(calls) == 2

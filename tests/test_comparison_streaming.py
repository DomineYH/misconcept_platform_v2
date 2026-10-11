"""Unbuffered ASGI comparison streams and independent child cancellation."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_comparison_concurrency import add_panels, panel_streams
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login

from src.models import ApiUsageLog, ComparisonRun
from src.services.call_admission import active_calls, registered_calls
from src.services.generation_lifecycle import active_runs

__all__ = ["client", "comparison", "scenario_payload", "student"]


@asynccontextmanager
async def live_comparison(
    client, comparison, disconnect_when=None, before_body=None
):
    from src.main import app

    disconnect = asyncio.Event()
    received = asyncio.Queue()
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return dict(
                type="http.request",
                body=json.dumps(comparison).encode(),
                more_body=False,
            )
        await disconnect.wait()
        return dict(type="http.disconnect")

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200, message
            if before_body is not None:
                await before_body()
            if disconnect_when == "headers":
                disconnect.set()
                await asyncio.Event().wait()
        if message["type"] == "http.response.body":
            body = message.get("body", b"").decode()
            if body.startswith("event:"):
                kind, payload = body.splitlines()[:2]
                await received.put((kind[7:], json.loads(payload[6:])))
                if kind[7:] == disconnect_when:
                    disconnect.set()
                    await asyncio.Event().wait()

    scope = dict(
        type="http",
        asgi={"version": "3.0", "spec_version": "2.0"},
        http_version="1.1",
        method="POST",
        scheme="http",
        path="/admin/comparisons",
        raw_path=b"/admin/comparisons",
        query_string=b"",
        root_path="",
        headers=[
            (b"content-type", b"application/json"),
            (b"cookie", f"session_id={client.cookies['session_id']}".encode()),
        ],
        client=("127.0.0.1", 9000),
        server=("test", 80),
    )
    task = asyncio.create_task(app(scope, receive, send))
    try:
        yield SimpleNamespace(
            task=task, received=received, disconnect=disconnect
        )
    finally:
        disconnect.set()
        await asyncio.wait_for(task, 10)


async def next_event(live, kind, panel_id):
    async with asyncio.timeout(10):
        while True:
            event, value = await live.received.get()
            if event == kind and value["panel_id"] == panel_id:
                return value


async def test_fast_panel_completes_before_slow_siblings_and_stop_is_independent(
    data, client, student, comparison
):
    login(client, data.admin)
    add_panels(comparison, 3)
    streams = panel_streams(student, comparison)
    for stream in streams[1:]:
        stream.events.insert(1, asyncio.Event())
    async with live_comparison(client, comparison) as live:
        completed = await next_event(live, "output.completed", "1")
        for stream in streams[1:]:
            await asyncio.wait_for(stream.read_started.wait(), 10)
        path = f"/admin/comparisons/{completed['comparison_run_id']}"
        state = (await client.get(path)).json()
        assert state["status"] == "running"
        assert [panel["status"] for panel in state["panels"]] == [
            "completed",
            "running",
            "running",
        ]
        assert len(active_calls) == 2
        assert len({permit.task for permit in active_calls}) == 2
        p1, p2, p3 = state["panels"]
        busy = await client.post(
            "/admin/comparisons",
            json={**comparison, "request_id": str(uuid4())},
        )
        assert (
            busy.status_code == 409
            and busy.json()["detail"]["comparison_run_id"]
            == completed["comparison_run_id"]
        )
        data.other.role = "admin"
        await data.db.commit()
        login(client, data.other)
        other_body = {
            **comparison,
            "request_id": str(uuid4()),
            "panels": comparison["panels"][:2],
        }
        limited = await client.post("/admin/comparisons", json=other_body)
        assert (
            limited.status_code == 429 and limited.headers["Retry-After"] == "1"
        )
        assert (
            student.responses.create.await_count == 3 and len(active_calls) == 2
        )
        assert (await client.get(path)).status_code == 404
        login(client, data.admin)
        invalid_all = await client.post(
            f"{path}/stop",
            json={
                "generations": [
                    {"panel_id": "2", "generation_id": p2["run_id"]},
                    {"panel_id": "3", "generation_id": str(uuid4())},
                ]
            },
        )
        assert invalid_all.status_code == 409 and len(active_calls) == 2
        assert [
            (panel["status"])
            for panel in (await client.get(path)).json()["panels"]
        ] == ["completed", "running", "running"]
        stop = await client.post(
            f"{path}/panels/1/stop", json={"generation_id": p1["run_id"]}
        )
        assert stop.status_code == 200
        assert stop.json()["panels"][0]["text"] == "Student answer"
        stale = await client.post(
            f"{path}/panels/2/stop", json={"generation_id": str(uuid4())}
        )
        assert stale.status_code == 409
        assert len(active_calls) == 2
        stop = await client.post(
            f"{path}/panels/2/stop", json={"generation_id": p2["run_id"]}
        )
        assert stop.status_code == 200
        assert stop.json()["status"] == "running"
        assert [panel["status"] for panel in stop.json()["panels"]] == [
            "completed",
            "cancelled",
            "running",
        ]
        cancelled = await next_event(live, "run.cancelled", "2")
        assert cancelled["text"] == "Student "
        assert streams[1].closed and streams[1].read_cancelled
        assert not streams[2].closed
        assert len(active_calls) == 1
        stop = await client.post(
            f"{path}/stop",
            json={
                "generations": [
                    {"panel_id": "3", "generation_id": p3["run_id"]}
                ]
            },
        )
        assert stop.status_code == 200
        assert stop.json()["status"] == "finished"
        await next_event(live, "run.cancelled", "3")
        await asyncio.wait_for(live.task, 10)
        final = (await client.get(path)).json()
        assert final["panels"][0] == state["panels"][0]
        assert [panel["status"] for panel in final["panels"]] == [
            "completed",
            "cancelled",
            "cancelled",
        ]
        assert all(panel["text"] == "Student " for panel in final["panels"][1:])
    assert all(stream.closed for stream in streams)
    assert all(stream.read_cancelled for stream in streams[1:])
    assert (
        student.responses.create.await_count == student.close.await_count == 3
    )
    assert not active_calls and not registered_calls and not active_runs


@pytest.mark.parametrize(
    "when", ["headers", "output.delta", "output.completed"]
)
async def test_disconnect_interrupts_only_unfinished_children_and_cleans_ledger(
    data, client, student, comparison, when
):
    login(client, data.admin)
    add_panels(comparison, 3)
    streams = panel_streams(student, comparison)
    for stream in streams[1:]:
        stream.events.insert(1, asyncio.Event())
    if when == "output.delta":
        streams[0].events.insert(1, asyncio.Event())
    async with live_comparison(client, comparison, when) as live:
        await asyncio.wait_for(live.task, 10)
    async with data.factory() as db:
        record = (await db.scalars(select(ComparisonRun))).one()
        ledger = (await db.scalars(select(ApiUsageLog))).all()
        assert all(
            entry.status != "running" and entry.finished_at is not None
            for entry in ledger
        )
        assert all(
            entry.session_id is None and entry.usage_scope == "comparison"
            for entry in ledger
        )
    state = (await client.get(f"/admin/comparisons/{record.id}")).json()
    assert state["status"] == "finished"
    assert [panel["status"] for panel in state["panels"]] == (
        ["completed", "interrupted", "interrupted"]
        if when == "output.completed"
        else ["interrupted"] * 3
    )
    if when == "output.completed":
        assert state["panels"][0]["text"] == "Student answer"
    if when == "headers":
        student.responses.create.assert_not_awaited()
        assert ledger == []
    else:
        assert all(
            stream.closed for stream in streams if stream.read_started.is_set()
        )
        assert student.close.await_count == student.responses.create.await_count
    assert not active_calls and not registered_calls and not active_runs


@pytest.mark.parametrize("when", ["before_body", "during_output"])
async def test_connection_revocation_interrupts_only_its_provider_child(
    data, client, student, comparison, monkeypatch, when
):
    import httpx2
    from test_anthropic_catalog import sdk_transport
    from test_anthropic_probes import stream_body
    from test_provider_connections import KEY, PASSWORD

    from src.models import ModelConfig, ProviderConnection
    from src.services.model_capabilities import capabilities
    from src.services.provider_secrets import encrypt_key

    login(client, data.admin)
    add_panels(comparison, 2)
    streams = panel_streams(student, comparison)
    streams[0].events.insert(1, asyncio.Event())
    connection = ProviderConnection(
        provider="anthropic",
        credential_revision=1,
    )
    data.db.add(connection)
    await data.db.flush()
    connection.encrypted_key, connection.nonce = encrypt_key(connection, KEY, 1)
    connection.encryption_key_version, connection.masked_hint = (
        "test-v1",
        "mock",
    )
    connection.enabled = True
    version = capabilities("anthropic", "claude-sonnet-4-6")[
        "definition_version"
    ]
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id="claude-sonnet-4-6",
        display_name="Other provider",
        enabled=True,
        capability_definition_version=version,
        verification_state={
            "student": dict(
                status="succeeded",
                credential_revision=1,
                connection_version=connection.connection_version,
                capability_definition_version=version,
                role_contract_version="s1-v1",
            )
        },
    )
    data.db.add(model)
    data.admin.set_password(PASSWORD)
    await data.db.commit()
    comparison["panels"][1]["model_config"] = dict(
        model_config_id=model.id,
        provider_connection_id=connection.id,
        provider="anthropic",
        model_id=model.model_id,
        options={"max_output_tokens": 1024, "thinking": {"type": "disabled"}},
    )
    opened, release = asyncio.Event(), asyncio.Event()

    async def upstream(request):
        opened.set()
        await release.wait()
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_body("Other provider answer"),
        )

    clients, calls = sdk_transport(
        monkeypatch, upstream, module="anthropic_generation"
    )

    async def revoke():
        disabled = await client.post(
            "/admin/ai/providers/openai/enabled",
            json={
                "current_password": PASSWORD,
                "expected_version": student.connection.connection_version,
                "enabled": False,
            },
        )
        assert disabled.status_code == 200, disabled.text

    async with live_comparison(
        client,
        comparison,
        before_body=revoke if when == "before_body" else None,
    ) as live:
        accepted = await next_event(live, "run.accepted", "1")
        await asyncio.wait_for(opened.wait(), 10)
        if when == "during_output":
            await asyncio.wait_for(streams[0].read_started.wait(), 10)
            assert (
                len(active_calls) == 2
                and len({permit.task for permit in active_calls}) == 2
            )
            await revoke()
        interrupted = await next_event(live, "run.interrupted", "1")
        assert interrupted["text"] == (
            "Student " if when == "during_output" else ""
        )
        if when == "during_output":
            assert streams[0].closed and streams[0].read_cancelled
        assert len(active_calls) == 1
        assert next(iter(active_calls)).provider == "anthropic"
        path = f"/admin/comparisons/{accepted['comparison_run_id']}"
        assert (await client.get(path)).json()["status"] == "running"
        release.set()
        completed = await next_event(live, "output.completed", "2")
        assert completed["text"] == "Other provider answer"
        await asyncio.wait_for(live.task, 10)
        assert (await client.get(path)).json()["status"] == "finished"
    assert len(calls) == 1
    assert student.responses.create.await_count == int(when == "during_output")
    assert all(sdk.is_closed() for sdk in clients)
    assert not active_calls and not registered_calls and not active_runs

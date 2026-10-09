"""Twenty teacher sessions and three admin jobs, through HTTP and mock SDKs."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import httpx2
import pytest
from lesson_fixtures import install_snapshot
from sqlalchemy import select, text
from starlette_csrf import CSRFMiddleware
from test_call_admission import catalog_transport
from test_call_execution import verified_model
from test_model_management import write
from test_provider_connections import KEY, PASSWORD, post
from test_scenario_api import login
from test_student_generation import frames
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import response_body, sdk_transport, sse

from src.config import config
from src.main import app
from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    ModelConfig,
    ProviderConnection,
    Session,
)
from src.services.call_admission import active_calls, registered_calls

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
api = probe_api
REPLACEMENT = "REPLACEMENT-KEY-SENTINEL-5678"


async def until(condition):
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("revoke", ["enabled", "delete"])
async def test_twenty_sessions_admin_headroom_rotation_and_revocation(
    data, api, monkeypatch, caplog, revoke
):
    await verified_model(data, api, monkeypatch)
    sessions = [data.session]
    for _ in range(19):
        session = Session(
            scenario_id=data.scenario.id, teacher_id=data.owner.id
        )
        data.db.add(session)
        sessions.append(session)
    await data.db.commit()
    connection = await data.db.get(ProviderConnection, 1)
    model = await data.db.get(ModelConfig, 1)
    for session in sessions:
        await install_snapshot(
            SimpleNamespace(
                db=data.db, scenario=data.scenario, session=session
            ),
            connection,
            model,
        )
    identities = [session.id for session in sessions]
    bodies = [dict(request_id=str(uuid4()), content="Why?") for _ in sessions]
    catalog_gates = [asyncio.Event() for _ in range(4)]
    student_gate = asyncio.Event()
    tasks = []

    async def catalog(request):
        index = len(lists) - 1
        await catalog_gates[index].wait()
        return httpx2.Response(200, json={"object": "list", "data": []})

    catalog_clients, lists = catalog_transport(monkeypatch, catalog)

    async def generation(request, payload):
        # Every real SDK request already has a committed attempt and no DB lock.
        async with data.factory() as db:
            assert await db.scalar(
                select(ApiUsageLog.id).where(
                    ApiUsageLog.request_id == admitted_request[0],
                    ApiUsageLog.status == "running",
                )
            )
        await student_gate.wait()
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.output_text.delta",
                delta="Answer",
                sequence_number=0,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.completed",
                response=response_body("Answer"),
                sequence_number=1,
            ),
        )

    admitted_request = []

    # Identify the winner from the public request input reaching the transport.
    async def student_upstream(request, payload):
        async with data.factory() as db:
            running = await db.scalar(
                select(GenerationRun).where(GenerationRun.status == "running")
            )
            admitted_request.append(running.request_id)
        return await generation(request, payload)

    student_clients, calls = sdk_transport(
        monkeypatch, student_upstream, budget=1500
    )
    protected = CSRFMiddleware(
        app,
        secret=config.SESSION_SECRET,
        cookie_name="csrftoken",
        header_name="x-csrf-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as teacher:
        login(teacher, data.owner)
        await teacher.get(f"/scenarios/{data.scenario.id}")
        headers = {"x-csrf-token": teacher.cookies["csrftoken"]}
        try:
            for _ in range(3):
                tasks.append(
                    asyncio.create_task(
                        write(
                            api, "providers/openai/catalog", expected_version=2
                        )
                    )
                )
                await until(lambda: len(lists) == len(tasks))
            refused = await write(
                api, "providers/openai/catalog", expected_version=2
            )
            assert refused.status_code == 429
            assert refused.headers["Retry-After"] == "1"
            lessons = [
                asyncio.create_task(
                    teacher.post(
                        f"/sessions/{identity}/turns/stream",
                        json=body,
                        headers=headers,
                    )
                )
                for identity, body in zip(identities, bodies)
            ]
            tasks.extend(lessons)
            await until(
                lambda: len(calls) == 1 and sum(t.done() for t in lessons) == 19
            )
            assert len(active_calls) == 4
            winner = next(i for i, t in enumerate(lessons) if not t.done())
            replay = await teacher.post(
                f"/sessions/{identities[winner]}/turns/stream",
                json=bodies[winner],
                headers=headers,
            )
            assert replay.json()["status"] == "running" and len(calls) == 1
            async with asyncio.timeout(2):
                async with data.engine.begin() as db:
                    await db.execute(
                        text("UPDATE app_setting SET updated_at='2026-10-09'")
                    )
            assert (
                await post(api, "key", 2, api_key=REPLACEMENT)
            ).status_code == 200
            student_gate.set()
            results = await asyncio.gather(*lessons)
            assert sorted(r.status_code for r in results) == [200] + [429] * 19
            assert frames(results[winner])[-1][0] == "output.completed"
            state = (await api.get("/admin/ai/state")).json()
            assert (
                state["models"][0]["verification_state"]["student"]["status"]
                == "stale"
            )
            blocked = await teacher.post(
                f"/sessions/{identities[winner]}/turns/stream",
                json=dict(request_id=str(uuid4()), content="After replacement"),
                headers=headers,
            )
            assert blocked.status_code == 503 and len(calls) == 1
            catalog_gates[0].set()
            assert (
                await tasks[0]
            ).status_code == 409  # Old-key cache cannot commit.

            # Admit a new revision while two old revisions remain registered.
            from test_call_admission import AsyncOpenAI

            from src.services import openai_catalog

            def replacement_client(**kwargs):
                assert (
                    kwargs["api_key"] == REPLACEMENT
                    and kwargs["max_retries"] == 0
                )

                async def upstream(request):
                    assert (
                        request.headers["authorization"]
                        == f"Bearer {REPLACEMENT}"
                    )
                    lists.append(request)
                    return await catalog(request)

                sdk = AsyncOpenAI(
                    **kwargs,
                    http_client=httpx2.AsyncClient(
                        transport=httpx2.MockTransport(upstream)
                    ),
                )
                catalog_clients.append(sdk)
                return sdk

            monkeypatch.setattr(
                openai_catalog, "AsyncOpenAI", replacement_client
            )
            tasks.append(
                asyncio.create_task(
                    write(api, "providers/openai/catalog", expected_version=3)
                )
            )
            await until(lambda: len(lists) == 4)
            assert (
                await post(
                    api,
                    revoke,
                    3,
                    **({"enabled": False} if revoke == "enabled" else {}),
                )
            ).status_code == 200
            async with asyncio.timeout(5):
                revoked = await asyncio.gather(
                    tasks[1], tasks[2], tasks[-1], return_exceptions=True
                )
            assert all(
                isinstance(r, asyncio.CancelledError) or r.status_code == 503
                for r in revoked
            )
        finally:
            student_gate.set()
            for gate in catalog_gates:
                gate.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    assert not active_calls and not registered_calls
    assert all(c.is_closed() for c in catalog_clients + student_clients)
    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.operation != "probe")
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert len(attempts) == len(lists) + len(calls) == 5
        assert (
            sorted(a.status for a in attempts)
            == ["cancelled"] * 3 + ["completed"] * 2
        )
        assert [a.credential_revision for a in attempts].count(1) == 4
        assert [a.credential_revision for a in attempts].count(2) == 1
        assert all(
            a.finished_at and a.estimated_cost_usd is None for a in attempts
        )
        assert len({(a.invocation_id, a.attempt_no) for a in attempts}) == 5
        assert len((await db.scalars(select(GenerationRun))).all()) == 1
        assert len((await db.scalars(select(Message))).all()) == 2
    dashboard = await api.get("/admin/api-usage")
    assert dashboard.status_code == 200
    for field, expected in (
        ("known-cost", "$0.000000"),
        ("unpriced-attempts", "3"),
        ("model-list-calls", "4"),
    ):
        assert (
            dashboard.text.split(f'id="{field}">')[1].split("</dd>")[0].strip()
            == expected
        )
    assert all(
        secret not in dashboard.text + caplog.text
        for secret in (KEY, REPLACEMENT, PASSWORD)
    )

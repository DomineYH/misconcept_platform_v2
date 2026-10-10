"""Synthetic delay/failure regression; no production latency/quality claim."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import httpx2
import pytest
from lesson_fixtures import install_snapshot, mentor_output
from sqlalchemy import select
from starlette_csrf import CSRFMiddleware
from test_call_admission import catalog_transport
from test_call_execution import verified_model
from test_model_management import write
from test_s1_load import until
from test_scenario_api import login
from test_student_generation import frames
from test_student_probe import api as api
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
from src.services.lesson_snapshots import canonical_hash

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
__all__ = ["api", "cleanup_probes"]


async def test_delayed_and_failed_mentors_leave_student_headroom_and_return_slots(
    data, api, monkeypatch
):
    await verified_model(data, api, monkeypatch)
    model = await data.db.get(ModelConfig, 1)
    connection = await data.db.get(ProviderConnection, 1)
    model.verification_state = {
        **model.verification_state,
        "mentor": {
            **model.verification_state["student"],
            "role_contract_version": "s3-v1",
        },
    }
    sessions = [
        data.session,
        Session(scenario_id=data.scenario.id, teacher_id=data.owner.id),
    ]
    data.db.add(sessions[1])
    await data.db.commit()
    for session in sessions:
        await install_snapshot(
            SimpleNamespace(
                db=data.db, scenario=data.scenario, session=session
            ),
            connection,
            model,
        )
        envelope = deepcopy(session.config_snapshot_json)
        envelope["config"]["mentor"].update(
            mode="manual",
            resolved_model_config=envelope["config"]["student"][
                "resolved_model_config"
            ],
        )
        session.config_snapshot_json = envelope
        session.config_hash = canonical_hash(envelope)
    await data.db.commit()
    release = asyncio.Event()
    mentor_inputs = []

    async def upstream(request, payload):
        if payload.get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    "response.completed",
                    response=response_body("Independent student"),
                    sequence_number=1,
                ),
            )
        mentor_inputs.append(payload)
        index = len(mentor_inputs)
        # Admission and the durable attempt precede waiting, with no writer lock.
        async with data.factory() as db:
            assert await db.scalar(
                select(ApiUsageLog.id).where(
                    ApiUsageLog.operation == "mentor",
                    ApiUsageLog.status == "running",
                )
            )
        await release.wait()
        if index == 1:
            return httpx2.Response(
                503,
                json={
                    "error": {
                        "message": "PRIVATE synthetic failure",
                        "type": "server_error",
                    }
                },
            )
        return httpx2.Response(
            200, json=response_body(mentor_output("Late coaching"))
        )

    clients, calls = sdk_transport(monkeypatch, upstream, budget=1500)

    async def catalog(request):
        await release.wait()
        return httpx2.Response(200, json={"object": "list", "data": []})

    catalog_clients, lists = catalog_transport(monkeypatch, catalog)
    protected = CSRFMiddleware(
        app, secret=config.SESSION_SECRET, header_name="x-csrf-token"
    )
    tasks = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as teacher:
        login(teacher, data.owner)
        await teacher.get(f"/scenarios/{data.scenario.id}")
        headers = {"x-csrf-token": teacher.cookies["csrftoken"]}

        async def student(session, content):
            result = await teacher.post(
                f"/sessions/{session.id}/turns/stream",
                json=dict(request_id=str(uuid4()), content=content),
                headers=headers,
            )
            assert frames(result)[-1][0] == "output.completed", result.text
            return frames(result)[-1][1]

        turns = [
            await student(session, f"Original target {i}")
            for i, session in enumerate(sessions)
        ]
        bodies = [
            dict(request_id=str(uuid4()), trigger="manual") for _ in sessions
        ]
        paths = [
            f"/sessions/{session.id}/turns/{turn['turn_id']}/mentor/stream"
            for session, turn in zip(sessions, turns)
        ]
        try:
            for path, body in zip(paths, bodies):
                tasks.append(
                    asyncio.create_task(
                        teacher.post(path, json=body, headers=headers)
                    )
                )
                await until(lambda: len(mentor_inputs) == len(tasks))
            tasks.append(
                asyncio.create_task(
                    write(api, "providers/openai/catalog", expected_version=2)
                )
            )
            await until(lambda: len(lists) == 1)
            refused = await write(
                api, "providers/openai/catalog", expected_version=2
            )
            assert (
                refused.status_code == 429
                and refused.headers["Retry-After"] == "1"
            )
            assert len(active_calls) == 3
            for path, body in zip(paths, bodies):
                replay = await teacher.post(path, json=body, headers=headers)
                assert replay.json()["status"] == "running"
            assert len(mentor_inputs) == 2
            later = [
                await student(session, f"Later independent input {i}")
                for i, session in enumerate(sessions)
            ]
            assert all(turn["turn_index"] == 2 for turn in later)
            assert not any(task.done() for task in tasks)
            assert len(mentor_inputs) == 2 and "Later independent" not in str(
                mentor_inputs
            )
            async with data.factory() as db:
                assert (
                    len(
                        (
                            await db.scalars(
                                select(Message).where(Message.role == "student")
                            )
                        ).all()
                    )
                    == 4
                )
                assert (
                    len(
                        (
                            await db.scalars(
                                select(Message).where(Message.role == "tutor")
                            )
                        ).all()
                    )
                    == 0
                )
            release.set()
            results = await asyncio.gather(*tasks)
            assert frames(results[0])[-1][0] == "run.failed"
            coaching = frames(results[1])[-1][1]
            assert (
                coaching["turn_id"] == turns[1]["turn_id"]
                and coaching["message"]["content"] == "Late coaching"
            )
            assert results[2].status_code == 200
            assert not active_calls and not registered_calls
            for path, body in zip(paths, bodies):
                assert (
                    await teacher.post(path, json=body, headers=headers)
                ).json()["status"] in ("failed", "completed")
            assert len(calls) == 6
            retry_path = f"/sessions/{sessions[0].id}/turns/{later[0]['turn_id']}/mentor/stream"
            retried = await teacher.post(
                retry_path,
                json=dict(request_id=str(uuid4()), trigger="manual"),
                headers=headers,
            )
            assert frames(retried)[-1][1]["result_kind"] == "message"
            assert (
                await write(api, "providers/openai/catalog", expected_version=2)
            ).status_code == 200
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert not active_calls and not registered_calls
    assert all(c.is_closed() for c in clients + catalog_clients)
    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.operation != "probe")
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert len(attempts) == len(calls) + len(lists) == 9
        mentors = [a for a in attempts if a.operation == "mentor"]
        assert [a.status for a in mentors] == [
            "failed",
            "completed",
            "completed",
        ]
        assert mentors[0].input_tokens is mentors[0].estimated_cost_usd is None
        assert all(a.finished_at and a.attempt_no == 1 for a in attempts)
        assert len({(a.invocation_id, a.attempt_no) for a in attempts}) == 9
        assert all(
            a.context_budget_json
            for a in attempts
            if a.operation in ("student", "mentor")
        )
        assert len((await db.scalars(select(GenerationRun))).all()) == 7
        assert len((await db.scalars(select(Message))).all()) == 10

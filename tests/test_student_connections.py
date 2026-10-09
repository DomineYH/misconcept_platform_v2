"""S1 lesson admission at authenticated HTTP and SDK transport boundaries."""

from uuid import uuid4

import httpx2
import pytest
from lesson_fixtures import LESSON_KEY, install_connection, install_snapshot
from test_scenario_api import client, login, scenario_payload
from test_student_generation import student
from test_student_probe import response_body, sdk_transport, sse

__all__ = ["client", "scenario_payload", "student"]


async def test_missing_db_connection_blocks_before_creating_a_turn(
    data, client, scenario_payload
):
    data.scenario.problem_situation = "Public problem"
    data.session.ended_at = None
    await data.db.commit()
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Why?"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_unavailable"
    assert "관리자" in response.json()["detail"]["message"]
    assert (
        await client.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 204


async def test_missing_student_snapshot_rejects_without_a_turn_or_attempt(
    data, client, student
):
    from sqlalchemy import select

    from src.models import ApiUsageLog, GenerationRun, Message

    data.session.config_snapshot_json = None
    await data.db.commit()
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Why?"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_unavailable"
    assert "관리자" in response.json()["detail"]["message"]
    assert "Prompt template" not in response.text
    student.responses.create.assert_not_awaited()
    async with data.factory() as db:
        for model in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(model))).all() == []


async def test_student_uses_db_key_exact_options_and_one_linked_attempt(
    data, client, scenario_payload, monkeypatch
):
    import json

    from openai import AsyncOpenAI
    from sqlalchemy import select
    from test_student_generation import frames

    from src.models import ApiUsageLog
    from src.services import openai_generation

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)
    from src.config import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    data.scenario.problem_situation = "Public problem"
    data.session.ended_at = None
    await data.db.commit()
    calls, clients = [], []

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {LESSON_KEY}"
        calls.append(json.loads(request.content))
        async with data.factory() as db:
            attempt = (await db.scalars(select(ApiUsageLog))).one()
            assert attempt.status == "running" and attempt.run_id
        payload = sse(
            "response.output_text.delta",
            delta="Answer",
            sequence_number=0,
            item_id="m",
            output_index=0,
            content_index=0,
        )
        payload += sse(
            "response.completed",
            response=response_body("Answer"),
            sequence_number=1,
        )
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=payload
        )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"].connect == 5
        sdk = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(sdk)
        return sdk

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    login(client, data.owner)
    body = {"request_id": str(uuid4()), "content": "Why?"}
    url = f"/sessions/{data.session.id}/turns/stream"
    response = await client.post(url, json=body)
    assert frames(response)[-1][0] == "output.completed"
    assert calls[0]["model"] == "gpt-5-mini"
    assert calls[0]["reasoning"] == {"effort": "medium"}
    assert calls[0]["max_output_tokens"] == 1500
    assert calls[0]["store"] is False
    replay = await client.post(url, json=body)
    assert replay.json()["status"] == "completed" and len(calls) == 1
    async with data.factory() as db:
        attempt = (await db.scalars(select(ApiUsageLog))).one()
        assert attempt.run_id == replay.json()["run_id"]
        assert attempt.session_id == data.session.id
        assert attempt.request_id == body["request_id"]
        assert attempt.operation == "student" and attempt.attempt_no == 1
        assert (
            attempt.status == "completed" and attempt.estimated_cost_usd is None
        )
    assert all(sdk.is_closed() for sdk in clients)


async def test_nonstream_student_has_an_invocation_without_a_fake_run(
    data, scenario_payload, monkeypatch
):
    from sqlalchemy import select

    from src.models import ApiUsageLog
    from src.services.student_bot import StudentBot

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)

    async def upstream(request, body):
        return httpx2.Response(200, json=response_body("Nonstream student"))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY
    )
    async with StudentBot(
        data.db, session_id=data.session.id, owner_id=data.owner.id
    ) as student:
        content, usage = await student.generate_response("Why?")
    assert content == "Nonstream student" and usage is None
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
    async with data.factory() as db:
        attempt = (await db.scalars(select(ApiUsageLog))).one()
        assert attempt.invocation_id and attempt.request_id
        assert attempt.run_id is None and attempt.status == "completed"


@pytest.mark.parametrize(
    "mode",
    [
        "different_model",
        "model_disabled",
        "unverified",
        "stale",
        "provider_disabled",
        "key_missing",
        "decryption_failed",
        "master_key_missing",
        "invalid_options",
    ],
)
async def test_unavailable_student_configuration_has_no_fallback_or_attempt(
    data, client, student, monkeypatch, mode, caplog
):
    from pydantic import SecretStr
    from sqlalchemy import select

    from src.config import config
    from src.models import ApiUsageLog, AppSetting

    setting = await data.db.get(AppSetting, 1)
    setting.student_model_config_id = student.model.id
    if mode == "different_model":
        student.model.model_id = "different-from-frozen-selection"
    elif mode == "model_disabled":
        student.model.enabled = False
    elif mode == "unverified":
        student.model.verification_state = {"student": {"status": "unverified"}}
    elif mode == "stale":
        student.connection.credential_revision += 1
    elif mode == "provider_disabled":
        student.connection.enabled = False
    elif mode == "key_missing":
        student.connection.enabled = False
        student.connection.encrypted_key = student.connection.nonce = None
        student.connection.masked_hint = (
            student.connection.encryption_key_version
        ) = None
    elif mode == "decryption_failed":
        student.connection.encrypted_key = b"unreadable ciphertext bytes"
    elif mode == "master_key_missing":
        monkeypatch.setattr(
            config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr("")
        )
    else:
        import copy

        from src.services.lesson_snapshots import canonical_hash

        envelope = copy.deepcopy(data.session.config_snapshot_json)
        envelope["config"]["student"]["resolved_model_config"]["options"][
            "reasoning"
        ]["effort"] = "none"
        data.session.config_snapshot_json = envelope
        data.session.config_hash = canonical_hash(envelope)
    await data.db.commit()
    login(client, data.owner)
    result = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Rejected question"},
    )
    assert (
        result.status_code == 503
        and result.json()["detail"]["code"] == "configuration_unavailable"
    )
    assert LESSON_KEY not in result.text + caplog.text
    student.responses.create.assert_not_awaited()
    assert (
        await client.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 204
    screen = await client.get(f"/scenarios/{data.scenario.id}")
    assert screen.status_code == 200
    assert "Public problem" in screen.text
    assert "PRIVATE ANALYSIS" not in screen.text
    async with data.factory() as db:
        assert (await db.scalars(select(ApiUsageLog))).all() == []


async def test_shared_capacity_rejects_before_reserving_a_student_turn(
    data, client, student
):
    import asyncio

    from sqlalchemy import select
    from test_student_generation import frames

    from src.models import ApiUsageLog, AppSetting, Session
    from src.services.call_admission import active_calls, registered_calls

    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = dict(
        total=2, openai=2, anthropic=2, google=2, admin=1
    )
    sessions = [data.session]
    for _ in range(2):
        session = Session(
            scenario_id=data.scenario.id,
            teacher_id=data.owner.id,
            **{
                key: getattr(data.session, key)
                for key in (
                    "config_snapshot_json",
                    "config_hash",
                    "source_scenario_version",
                    "snapshot_origin",
                    "snapshot_created_at",
                )
            },
        )
        data.db.add(session)
        sessions.append(session)
    await data.db.commit()
    login(client, data.owner)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    tasks = []
    try:
        for session in sessions[:2]:
            tasks.append(
                asyncio.create_task(
                    client.post(
                        f"/sessions/{session.id}/turns/stream",
                        json={
                            "request_id": str(uuid4()),
                            "content": "Admitted",
                        },
                    )
                )
            )
            async with asyncio.timeout(5):
                while student.responses.create.await_count < len(tasks):
                    await asyncio.sleep(0.01)
        rejected = await client.post(
            f"/sessions/{sessions[2].id}/turns/stream",
            json={"request_id": str(uuid4()), "content": "Capacity refused"},
        )
        assert (
            rejected.status_code == 429
            and rejected.json()["detail"]["code"] == "call_limit_reached"
        )
        assert (
            await client.get(f"/sessions/{sessions[2].id}/messages/updates")
        ).status_code == 204
    finally:
        gate.set()
        results = await asyncio.gather(*tasks)
    assert all(
        frames(result)[-1][0] == "output.completed" for result in results
    )
    assert student.responses.create.await_count == 2
    async with data.factory() as db:
        assert len((await db.scalars(select(ApiUsageLog))).all()) == 2
    assert not active_calls and not registered_calls

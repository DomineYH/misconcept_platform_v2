"""Live authorization and provider revocation after native student admission."""

from uuid import uuid4

import pytest
from test_scenario_api import login
from test_student_generation import client, frames, scenario_payload, student

__all__ = ["client", "scenario_payload", "student"]


@pytest.mark.parametrize(
    "revocation", ["activation", "group", "assignment", "model", "role", "key"]
)
async def test_student_rechecks_live_access_after_sse_acceptance(
    data, client, student, revocation
):
    import httpx
    from sqlalchemy import delete

    from src.main import app
    from src.models import (
        ModelConfig,
        ProviderConnection,
        Scenario,
        ScenarioGroup,
        User,
    )

    async def boundary(scope, receive, send):
        async def accepted(message):
            if message[
                "type"
            ] == "http.response.body" and b"event: run.accepted" in message.get(
                "body", b""
            ):
                async with data.factory() as db:
                    if revocation == "activation":
                        scenario = await db.get(Scenario, data.scenario.id)
                        scenario.is_active = False
                    elif revocation == "group":
                        user = await db.get(User, data.owner.id)
                        user.group_id = None
                    elif revocation == "assignment":
                        await db.execute(
                            delete(ScenarioGroup).where(
                                ScenarioGroup.scenario_id == data.scenario.id
                            )
                        )
                    elif revocation in ("model", "role"):
                        model = await db.get(ModelConfig, student.model.id)
                        if revocation == "model":
                            model.enabled = False
                        else:
                            model.verification_state = {
                                "student": {"status": "stale"}
                            }
                    else:
                        provider = await db.get(
                            ProviderConnection, student.connection.id
                        )
                        provider.enabled = False
                    await db.commit()
            await send(message)

        await app(scope, receive, accepted)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary), base_url="http://test"
    ) as connection:
        login(connection, data.owner)
        response = await connection.post(
            f"/sessions/{data.session.id}/turns/stream",
            json=dict(
                request_id=str(uuid4()), content="Permission revoked before SDK"
            ),
        )
    assert frames(response)[-1][0] == "run.failed"
    assert frames(response)[-1][1]["code"] == "configuration_unavailable"
    student.responses.create.assert_not_awaited()


@pytest.mark.parametrize("operation", ["enabled", "delete"])
async def test_native_student_key_rotation_then_revocation_cancels_active_call(
    data, client, student, operation
):
    import asyncio

    from sqlalchemy import select
    from test_provider_connections import PASSWORD

    from src.models import ApiUsageLog
    from src.services.call_admission import active_calls, registered_calls

    data.admin.set_password(PASSWORD)
    await data.db.commit()
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    login(client, data.owner)
    body = dict(request_id=str(uuid4()), content="Waiting for provider")
    running = asyncio.create_task(
        client.post(f"/sessions/{data.session.id}/turns/stream", json=body)
    )
    try:
        async with asyncio.timeout(5):
            await student.stream.read_started.wait()
        login(client, data.admin)
        changed = await client.post(
            "/admin/ai/providers/openai/key",
            json=dict(
                expected_version=student.connection.connection_version,
                current_password=PASSWORD,
                api_key="sk-SYNTHETIC-REPLACEMENT",
            ),
        )
        assert changed.status_code == 200, changed.text
        assert not running.done() and not student.stream.read_cancelled
        payload = dict(
            expected_version=student.connection.connection_version + 1,
            current_password=PASSWORD,
        )
        if operation == "enabled":
            payload["enabled"] = False
        revoked = await client.post(
            f"/admin/ai/providers/openai/{operation}", json=payload
        )
        assert revoked.status_code == 200, revoked.text
        response = await asyncio.wait_for(asyncio.shield(running), 5)
        assert frames(response)[-1][0] == "run.failed"
        assert frames(response)[-1][1]["code"] == "configuration_unavailable"
        assert student.stream.read_cancelled and student.stream.closed
        assert student.responses.create.await_count == 1
        async with data.factory() as db:
            attempt = (await db.scalars(select(ApiUsageLog))).one()
            assert (attempt.status, attempt.error_code) == (
                "cancelled",
                "interrupted",
            )
    finally:
        gate.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert not active_calls and not registered_calls


async def test_inactive_scenario_blocks_admin_owner_before_creating_turn(
    data, client, student
):
    data.session.teacher_id = data.admin.id
    data.scenario.is_active = False
    await data.db.commit()
    login(client, data.admin)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json=dict(request_id=str(uuid4()), content="Inactive lesson"),
    )
    assert response.status_code == 404
    student.responses.create.assert_not_awaited()
    assert (
        await client.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 204

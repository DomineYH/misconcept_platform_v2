"""Shared capacity and credential revocation through authenticated HTTP."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_mentor_generation import (
    client,
    complete_turn,
    mentor,
    mentor_url,
    scenario_payload,
    student,
)
from test_mentor_lifecycle import waiting_mentor
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog, AppSetting, Session
from src.services.call_admission import active_calls, registered_calls

__all__ = ["client", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize("trigger", ["manual", "auto"])
async def test_capacity_refusal_has_no_run_counter_or_attempt_and_can_be_retried(
    data, client, mentor, trigger
):
    from lesson_fixtures import configure_mentor

    await configure_mentor(
        data, mentor, "auto" if trigger == "auto" else "manual", start_turn=1
    )
    data.scenario.tutor_sensitivity = "high"
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
    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = dict(
        total=2, openai=2, anthropic=2, google=2, admin=1
    )
    await data.db.commit()
    login(client, data.owner)
    first = await complete_turn(client, data)
    second_data = SimpleNamespace(session=sessions[1])
    second = await complete_turn(client, second_data)
    gate, entered = asyncio.Event(), asyncio.Event()
    mentor.stream.events.insert(0, gate)

    async def upstream(**body):
        if body.get("stream"):
            return mentor.stream
        entered.set()
        await gate.wait()
        return SimpleNamespace(
            output_text=(
                '{"is_repetitive":true,"is_inappropriate":false,"reason":"Condition met"}'
                if "text" in body
                else "Capacity coaching"
            ),
            usage=None,
        )

    mentor.responses.create.side_effect = upstream
    tasks = [
        asyncio.create_task(
            client.post(
                mentor_url(data, first),
                json={"request_id": str(uuid4()), "trigger": trigger},
            )
        )
    ]
    payload = {"request_id": str(uuid4()), "trigger": trigger}
    try:
        await asyncio.wait_for(entered.wait(), 5)
        calls_before = mentor.responses.create.await_count
        tasks.append(
            asyncio.create_task(
                client.post(
                    f"/sessions/{sessions[2].id}/turns/stream",
                    json={
                        "request_id": str(uuid4()),
                        "content": "Independent student",
                    },
                )
            )
        )
        async with asyncio.timeout(5):
            while mentor.responses.create.await_count == calls_before:
                await asyncio.sleep(0.01)
        response = await client.post(
            mentor_url(second_data, second), json=payload
        )
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "call_limit_reached"
        assert response.headers["Retry-After"] == "1"
        assert (
            await client.get(
                f"/sessions/{sessions[1].id}/runs",
                params=payload,
            )
        ).status_code == 404
        await data.db.refresh(sessions[1])
        assert sessions[1].tutor_question_count == 0
        async with data.factory() as db:
            assert (
                await db.scalars(
                    select(ApiUsageLog).where(
                        ApiUsageLog.request_id == payload["request_id"]
                    )
                )
            ).all() == []
    finally:
        gate.set()
        results = await asyncio.gather(*tasks)
    assert all(
        frames(result)[-1][0] == "output.completed" for result in results
    )
    retry = await client.post(mentor_url(second_data, second), json=payload)
    assert frames(retry)[-1][1]["result_kind"] == "message"
    await data.db.refresh(sessions[1])
    assert sessions[1].tutor_question_count == 1
    assert not active_calls and not registered_calls


@pytest.mark.parametrize("operation", ["mentor", "mentor_judgment"])
async def test_disabling_connection_cancels_mentor_and_finalizes_unknown_usage(
    data, client, mentor, caplog, operation
):
    from lesson_fixtures import LESSON_KEY

    if operation == "mentor":
        turn, entered, cancelled = await waiting_mentor(data, client, mentor)
    else:
        from lesson_fixtures import configure_mentor

        await configure_mentor(data, mentor, "auto", start_turn=1)
        data.scenario.tutor_sensitivity = "high"
        await data.db.commit()
        login(client, data.owner)
        await complete_turn(client, data, "Explain how you solved the problem")
        mentor.stream.events[-1].response.output_text = (
            "Entirely distinct solution"
        )
        turn = await complete_turn(
            client, data, "Describe another representation"
        )
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def upstream(**body):
            assert body["max_output_tokens"] == 1500
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        mentor.responses.create.side_effect = upstream
        mentor.close.reset_mock()
    data.admin.set_password("A13-admin-password")
    await data.db.commit()
    payload = {
        "request_id": str(uuid4()),
        "trigger": "manual" if operation == "mentor" else "auto",
    }
    pending = asyncio.create_task(
        client.post(mentor_url(data, turn), json=payload)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        login(client, data.admin)
        response = await client.post(
            "/admin/ai/providers/openai/enabled",
            json={
                "current_password": "A13-admin-password",
                "expected_version": mentor.connection.connection_version,
                "enabled": False,
            },
        )
        assert response.status_code == 200, response.text
        result = await asyncio.wait_for(asyncio.shield(pending), 5)
        assert frames(result)[-1][0] == "run.interrupted"
        assert cancelled.is_set()
        mentor.close.assert_awaited_once()
        assert LESSON_KEY not in response.text + result.text + caplog.text
        async with data.factory() as db:
            attempt = (
                await db.scalars(
                    select(ApiUsageLog).where(
                        ApiUsageLog.request_id == payload["request_id"]
                    )
                )
            ).one()
            assert (
                attempt.operation == operation and attempt.status == "cancelled"
            )
            assert (
                attempt.error_code == "interrupted"
                and attempt.finished_at is not None
            )
            assert (
                attempt.input_tokens is None and attempt.output_tokens is None
            )
        await data.db.refresh(data.session)
        assert data.session.tutor_intervention_count == 0
        assert not active_calls and not registered_calls
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

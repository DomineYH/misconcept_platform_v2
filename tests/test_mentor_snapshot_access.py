"""Live access and one overall deadline for snapshot mentor execution."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from lesson_fixtures import configure_mentor, mentor_output
from sqlalchemy import delete, select
from test_mentor_generation import (
    client,
    complete_turn,
    mentor,
    mentor_url,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.main import app
from src.models import (
    ApiUsageLog,
    AppSetting,
    ModelConfig,
    ProviderConnection,
    Scenario,
    ScenarioGroup,
    User,
)

__all__ = ["client", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize(
    "revocation", ["activation", "group", "assignment", "model", "role", "key"]
)
async def test_mentor_rechecks_live_access_after_acceptance(
    data, client, mentor, revocation
):
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()

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
                        model = await db.get(
                            ModelConfig, mentor.mentor_model.id
                        )
                        if revocation == "model":
                            model.enabled = False
                        else:
                            model.verification_state = {
                                "mentor": {"status": "stale"}
                            }
                    else:
                        connection = await db.get(
                            ProviderConnection, mentor.connection.id
                        )
                        connection.enabled = False
                    await db.commit()
            await send(message)

        await app(scope, receive, accepted)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary), base_url="http://test"
    ) as connection:
        login(connection, data.owner)
        result = await connection.post(
            mentor_url(data, turn),
            json={"request_id": str(uuid4()), "trigger": "manual"},
        )
    assert frames(result)[-1][1]["code"] == "configuration_unavailable"
    mentor.responses.create.assert_not_awaited()


async def test_single_auto_call_respects_overall_deadline(data, client, mentor):
    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {
        **setting.timeouts_json,
        "mentor_total": 1,
        "mentor_first_output": 1,
    }
    await data.db.commit()
    calls = []

    async def slow(**body):
        calls.append(body)
        await asyncio.sleep(1.1)
        return SimpleNamespace(
            output_text=(mentor_output("Too late coaching", "Condition met")),
            usage=None,
        )

    mentor.responses.create.side_effect = slow
    request_id = str(uuid4())
    result = await client.post(
        mentor_url(data, turn),
        json={"request_id": request_id, "trigger": "auto"},
    )
    assert frames(result)[-1][0] == "run.failed"
    assert frames(result)[-1][1]["code"] == "timeout_total"
    assert len(calls) == 1
    assert "Too late coaching" not in result.text
    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.request_id == request_id)
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert [(row.operation, row.status) for row in attempts] == [
            ("mentor", "timed_out"),
        ]


async def test_auto_failure_is_consumed_once_and_manual_retry_is_allowed(
    data, client, mentor
):
    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    mentor.responses.create.side_effect = RuntimeError("PRIVATE ERROR")
    failed = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert frames(failed)[-1][0] == "run.failed"
    again = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert again.json()["run_id"] == frames(failed)[0][1]["run_id"]
    assert mentor.responses.create.await_count == 1
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output("Manual recovery"), usage=None
    )
    retry = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert frames(retry)[-1][1]["message"]["content"] == "Manual recovery"
    assert mentor.responses.create.await_count == 2


async def test_inflight_auto_check_reserves_rolling_capacity_until_negative_result(
    data, client, mentor
):
    await configure_mentor(
        data, mentor, "auto", start_turn=1, window_turns=3, max_interventions=1
    )
    login(client, data.owner)
    first = await complete_turn(client, data)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def upstream(**body):
        if body.get("stream"):
            return mentor.stream
        if "자동 검사" in body["instructions"]:
            entered.set()
            await gate.wait()
            return SimpleNamespace(
                output_text=mentor_output(None, "Condition not met"),
                usage=None,
            )
        return SimpleNamespace(
            output_text=mentor_output("Released reservation coaching"),
            usage=None,
        )

    mentor.responses.create.side_effect = upstream
    request_id = str(uuid4())
    pending = asyncio.create_task(
        client.post(
            mentor_url(data, first),
            json={"request_id": request_id, "trigger": "auto"},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        latest = await complete_turn(
            client, data, "Next student is independent"
        )
        denied_id = str(uuid4())
        denied = await client.post(
            mentor_url(data, latest),
            json={"request_id": denied_id, "trigger": "manual"},
        )
        assert (
            denied.status_code == 409
            and denied.json()["detail"]["code"] == "mentor_limit"
        )
        assert (
            await client.get(
                f"/sessions/{data.session.id}/runs",
                params={"request_id": denied_id},
            )
        ).status_code == 404
        replay = await client.post(
            mentor_url(data, first),
            json={"request_id": request_id, "trigger": "auto"},
        )
        assert replay.json()["status"] == "running"
    finally:
        gate.set()
        result = await pending
    assert frames(result)[-1][1]["result_kind"] == "no_intervention"
    released = await client.post(
        mentor_url(data, latest),
        json={"request_id": denied_id, "trigger": "manual"},
    )
    assert (
        frames(released)[-1][1]["message"]["content"]
        == "Released reservation coaching"
    )


@pytest.mark.parametrize("outcome", ["negative", "failure"])
async def test_admitted_auto_checks_consume_interval_without_consuming_interventions(
    data, client, mentor, outcome
):
    await configure_mentor(
        data,
        mentor,
        "auto",
        start_turn=1,
        min_interval_turns=2,
        max_interventions=1,
    )
    login(client, data.owner)
    first = await complete_turn(client, data)
    checks = 0

    async def upstream(**body):
        nonlocal checks
        if body.get("stream"):
            return mentor.stream
        checks += 1
        if outcome == "failure" and checks == 1:
            raise RuntimeError("PRIVATE failure")
        return SimpleNamespace(
            output_text=mentor_output(None, "Condition not met"),
            usage=None,
        )

    mentor.responses.create.side_effect = upstream
    checked = await client.post(
        mentor_url(data, first),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert frames(checked)[-1][0] == (
        "run.failed" if outcome == "failure" else "output.completed"
    )
    second = await complete_turn(client, data)
    denied = await client.post(
        mentor_url(data, second),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert (
        denied.status_code == 409
        and denied.json()["detail"]["code"] == "mentor_interval"
    )
    third = await complete_turn(client, data)
    admitted = await client.post(
        mentor_url(data, third),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert frames(admitted)[-1][1]["result_kind"] == "no_intervention"
    assert checks == 2
    await data.db.refresh(data.session)
    assert data.session.tutor_intervention_count == 0

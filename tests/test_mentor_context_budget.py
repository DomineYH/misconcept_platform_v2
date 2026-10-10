"""Mentor budgeting at authenticated HTTP, durable SQLite and SDK boundaries."""

from uuid import uuid4

import pytest
from lesson_fixtures import configure_mentor
from sqlalchemy import select
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

from src.models import ApiUsageLog, GenerationRun, Message, Session
from src.services import context_budget
from src.services.call_admission import active_calls, registered_calls
from src.services.model_capabilities import capabilities

__all__ = ["client", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize("required", ["target", "instruction", "condition"])
async def test_oversized_required_mentor_input_is_preserved(
    data, client, mentor, monkeypatch, required
):
    from copy import deepcopy

    from src.services.lesson_snapshots import canonical_hash

    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(
        client,
        data,
        "한🙂 {target}" * 400 if required == "target" else "Target",
    )
    envelope = deepcopy(data.session.config_snapshot_json)
    if required == "instruction":
        envelope["config"]["mentor"]["behavior_instruction"] = (
            "PRIVATE FIXED 한🙂" * 1200
        )
    elif required == "condition":
        envelope["config"]["mentor"]["intervention_policy"]["condition"] = (
            "PRIVATE CONDITION 한🙂" * 1200
        )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.commit()
    messages = (
        await data.db.scalars(select(Message.content).order_by(Message.id))
    ).all()
    mentor.responses.create.reset_mock()
    definition = capabilities("openai", "gpt-5.2")
    definition["combined_context_tokens"] = 8500
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    rejected = await client.post(
        mentor_url(data, turn),
        json=dict(request_id=str(uuid4()), trigger="auto"),
    )
    assert (
        rejected.status_code == 422
        and rejected.json()["detail"]["code"] == "context_limit"
    )
    assert "PRIVATE" not in rejected.text and "{target}" not in rejected.text
    mentor.responses.create.assert_not_awaited()
    async with data.factory() as db:
        assert (
            await db.scalars(select(Message.content).order_by(Message.id))
        ).all() == messages
        assert (
            await db.scalars(
                select(GenerationRun).where(GenerationRun.operation == "mentor")
            )
        ).all() == []
        assert (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).all() == []
        session = await db.get(Session, data.session.id)
        assert (
            session.config_snapshot_json == envelope
            and session.config_hash == canonical_hash(envelope)
        )
        assert (
            session.tutor_question_count
            == session.tutor_intervention_count
            == 0
        )


@pytest.mark.parametrize("trigger", ["manual", "auto"])
@pytest.mark.parametrize(
    "capacity,status,code",
    [(1600, 422, "context_limit"), (None, 503, "configuration_unavailable")],
)
async def test_mentor_preflight_preserves_policy_and_can_be_retried(
    data, client, mentor, monkeypatch, trigger, capacity, status, code
):
    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(client, data, "Target 한🙂 {x}")
    mentor.responses.create.reset_mock()
    definition = capabilities("openai", "gpt-5.2")
    if capacity is None:
        definition.pop("combined_context_tokens")
    else:
        definition["combined_context_tokens"] = capacity
    with monkeypatch.context() as small:
        small.setattr(context_budget, "capabilities", lambda *_: definition)
        payload = dict(request_id=str(uuid4()), trigger=trigger)
        rejected = await client.post(mentor_url(data, turn), json=payload)
    assert rejected.status_code == status, rejected.text
    assert rejected.json()["detail"]["code"] == code
    assert "관리자" in rejected.json()["detail"]["message"]
    assert (
        "PRIVATE" not in rejected.text and "context_budget" not in rejected.text
    )
    mentor.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        assert (
            await db.scalars(
                select(GenerationRun).where(GenerationRun.operation == "mentor")
            )
        ).all() == []
        assert (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).all() == []
        session = await db.get(Session, data.session.id)
        assert (
            session.tutor_question_count
            == session.tutor_intervention_count
            == 0
        )
    accepted = await client.post(mentor_url(data, turn), json=payload)
    assert frames(accepted)[-1][1]["result_kind"] == "message"
    assert mentor.responses.create.await_count == 1


async def test_provider_context_failure_consumes_interval_but_rejected_retry_does_not(
    data, client, mentor, monkeypatch
):
    import httpx2
    from openai import BadRequestError

    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    mentor.responses.create.side_effect = BadRequestError(
        "PRIVATE provider context",
        response=httpx2.Response(
            400, request=httpx2.Request("POST", "https://provider.test")
        ),
        body={"code": "context_length_exceeded"},
    )
    payload = dict(request_id=str(uuid4()), trigger="auto")
    failed = await client.post(mentor_url(data, turn), json=payload)
    final = frames(failed)[-1][1]
    assert final["code"] == "context_limit"
    async with data.factory() as db:
        before = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).one()
        budget = before.context_budget_json
        assert budget and before.status == "failed"
        assert before.input_tokens is before.estimated_cost_usd is None
        original_messages = (await db.scalars(select(Message.id))).all()
    definition = capabilities("openai", "gpt-5.2")
    definition["combined_context_tokens"] = 1600
    with monkeypatch.context() as small:
        small.setattr(context_budget, "capabilities", lambda *_: definition)
        replay = await client.post(mentor_url(data, turn), json=payload)
        automatic = await client.post(
            mentor_url(data, turn),
            json=dict(request_id=str(uuid4()), trigger="auto"),
        )
        rejected = await client.post(
            mentor_url(data, turn),
            json=dict(request_id=str(uuid4()), trigger="manual"),
        )
    assert (
        replay.json()["run_id"] == automatic.json()["run_id"] == final["run_id"]
    )
    assert rejected.status_code == 422
    assert mentor.responses.create.await_count == 1
    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).all()
        assert len(attempts) == 1 and attempts[0].context_budget_json == budget
        assert (await db.scalars(select(Message.id))).all() == original_messages
        runs = (
            await db.scalars(
                select(GenerationRun).where(GenerationRun.operation == "mentor")
            )
        ).all()
        assert len(runs) == 1 and runs[0].status == "failed"
        session = await db.get(Session, data.session.id)
        assert (
            session.tutor_question_count == 1
            and session.tutor_intervention_count == 0
        )
    from types import SimpleNamespace

    from lesson_fixtures import mentor_output

    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = mentor.stream
    next_turn = await complete_turn(client, data, "Later target")
    denied = await client.post(
        mentor_url(data, next_turn),
        json=dict(request_id=str(uuid4()), trigger="auto"),
    )
    assert (
        denied.status_code == 409
        and denied.json()["detail"]["code"] == "mentor_interval"
    )
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output("Manual retry"), usage=None
    )
    retry = await client.post(
        mentor_url(data, next_turn),
        json=dict(request_id=str(uuid4()), trigger="manual"),
    )
    assert frames(retry)[-1][1]["message"]["content"] == "Manual retry"
    assert (
        mentor.responses.create.await_count == 3
    )  # One student and two mentor attempts.
    assert "PRIVATE" not in failed.text and "context_budget" not in str(
        replay.json()
    )


async def test_mentor_ledger_failure_never_calls_provider_and_releases_capacity(
    data, client, mentor
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import OperationalError

    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()

    def reject(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO api_usage_log"):
            raise OperationalError(
                statement, parameters, Exception("Injected ledger failure")
            )

    sql_event.listen(data.engine.sync_engine, "before_cursor_execute", reject)
    try:
        result = await client.post(
            mentor_url(data, turn),
            json=dict(request_id=str(uuid4()), trigger="manual"),
        )
    finally:
        sql_event.remove(
            data.engine.sync_engine, "before_cursor_execute", reject
        )
    assert frames(result)[-1][1]["code"] == "configuration_unavailable"
    mentor.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        assert (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).all() == []
        run = (
            await db.scalars(
                select(GenerationRun).where(GenerationRun.operation == "mentor")
            )
        ).one()
        assert (
            run.status == "failed"
            and run.result_kind is run.mentor_reason_summary is None
        )
        session = await db.get(Session, data.session.id)
        assert session.tutor_intervention_count == 0


async def test_mentor_context_guidance_follows_ownership_and_csrf(
    data, client, mentor, monkeypatch
):
    import httpx
    from starlette_csrf import CSRFMiddleware

    from src.config import config
    from src.main import app

    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    definition = capabilities("openai", "gpt-5.2")
    definition["combined_context_tokens"] = 1600
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    protected = CSRFMiddleware(
        app,
        secret=config.SESSION_SECRET,
        cookie_name="csrftoken",
        header_name="x-csrf-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as api:
        payload = dict(request_id=str(uuid4()), trigger="manual")
        login(api, data.other)
        await api.get(f"/scenarios/{data.scenario.id}")
        denied = await api.post(
            mentor_url(data, turn),
            json=payload,
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert denied.status_code == 403 and "context_limit" not in denied.text
        login(api, data.owner)
        await api.get(f"/scenarios/{data.scenario.id}")
        assert (
            await api.post(mentor_url(data, turn), json=payload)
        ).status_code == 403
        rejected = await api.post(
            mentor_url(data, turn),
            json=payload,
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert (
            rejected.status_code == 422
            and rejected.json()["detail"]["code"] == "context_limit"
        )
    mentor.responses.create.assert_not_awaited()

"""Student context admission through authenticated HTTP, SQLite and SDK transport."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from test_scenario_api import login
from test_student_generation import (
    client,
    event,
    frames,
    scenario_payload,
    student,
)

from src.models import ApiUsageLog, GenerationRun, Message
from src.services import context_budget
from src.services.model_capabilities import capabilities

__all__ = ["client", "scenario_payload", "student"]


async def test_nonstream_preflight_does_not_save_teacher_or_consume_capacity(
    data, student, monkeypatch
):
    from src.services.call_admission import active_calls, registered_calls
    from src.services.invocation_types import InvocationError
    from src.services.session_mgr import SessionManager

    definition = capabilities("openai", "gpt-5-mini")
    definition["combined_context_tokens"] = 1600
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    manager = SessionManager(data.db, data.session.id)
    try:
        with pytest.raises(InvocationError, match="context_limit"):
            await manager.process_teacher_message("Why 한🙂 {x}?")
    finally:
        await manager.close()
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        for model in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(model))).all() == []


@pytest.mark.parametrize(
    "capacity,code,status",
    [(1600, "context_limit", 422), (None, "configuration_unavailable", 503)],
)
async def test_required_input_rejection_creates_no_turn_run_or_attempt(
    data, client, student, monkeypatch, capacity, code, status
):
    definition = capabilities("openai", "gpt-5-mini")
    if capacity is None:
        definition.pop("combined_context_tokens")
    else:
        definition["combined_context_tokens"] = capacity
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    login(client, data.owner)
    result = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Why 한🙂 {x}?"},
    )
    assert result.status_code == status, result.text
    assert result.json()["detail"]["code"] == code
    assert "관리자" in result.json()["detail"]["message"]
    assert "Student profile" not in result.text
    assert "context_budget" not in result.text
    student.responses.create.assert_not_awaited()
    async with data.factory() as db:
        for model in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(model))).all() == []


async def test_provider_context_failure_is_one_attempt_and_rejected_retry_preserves_it(
    data, client, student, monkeypatch
):
    student.stream.events = [
        event("error", code="context_length_exceeded", message="PRIVATE")
    ]
    login(client, data.owner)
    url = f"/sessions/{data.session.id}/turns/stream"
    payload = dict(request_id=str(uuid4()), content="Original question 한🙂")
    result = await client.post(url, json=payload)
    assert frames(result)[-1][1]["code"] == "context_limit"
    previous = (
        await client.get(
            f"/sessions/{data.session.id}/runs",
            params={"request_id": payload["request_id"]},
        )
    ).json()
    assert previous["status"] == "failed"
    async with data.factory() as db:
        before_attempt = (await db.scalars(select(ApiUsageLog))).one()
        budget = before_attempt.context_budget_json
        assert budget and before_attempt.status == "failed"
        assert (
            before_attempt.input_tokens is None
            and before_attempt.estimated_cost_usd is None
        )
        assert before_attempt.raw_usage_json is None
    definition = capabilities("openai", "gpt-5-mini")
    definition["combined_context_tokens"] = 1600
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    retry = await client.post(
        url,
        json=dict(
            request_id=str(uuid4()),
            turn_id=previous["turn_id"],
            content=payload["content"],
        ),
    )
    assert retry.status_code == 422
    assert retry.json()["detail"]["code"] == "context_limit"
    assert (await client.post(url, json=payload)).json() == previous
    assert student.responses.create.await_count == 1
    async with data.factory() as db:
        assert len((await db.scalars(select(GenerationRun))).all()) == 1
        teacher = (await db.scalars(select(Message))).one()
        assert (
            teacher.content == payload["content"]
            and teacher.turn_id == previous["turn_id"]
        )
        attempt = (await db.scalars(select(ApiUsageLog))).one()
        assert attempt.context_budget_json == budget
    assert "context_budget" not in str(previous)
    assert "estimated_input_tokens" not in result.text


@pytest.mark.parametrize("large_instruction", [False, True])
async def test_fixed_input_is_preserved_and_shortened_question_can_be_admitted(
    data, client, student, monkeypatch, large_instruction
):
    from copy import deepcopy

    from lesson_fixtures import STUDENT_INSTRUCTION

    from src.services.invocation_types import TextRequest
    from src.services.lesson_snapshots import canonical_hash

    required = TextRequest(
        "openai",
        "gpt-5-mini",
        "student",
        STUDENT_INSTRUCTION,
        [{"role": "user", "content": "x"}],
        {},
        "fixture",
    )
    definition = capabilities("openai", "gpt-5-mini")
    definition["combined_context_tokens"] = (
        context_budget.estimate_input(required) + 1500
    )
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    if large_instruction:
        envelope = deepcopy(data.session.config_snapshot_json)
        envelope["config"]["student"]["internal_profile"] = (
            "FIXED-INSTRUCTION 한🙂" * 500
        )
        data.session.config_snapshot_json = envelope
        data.session.config_hash = canonical_hash(envelope)
        await data.db.commit()
    original_snapshot = deepcopy(data.session.config_snapshot_json)
    original_hash = data.session.config_hash
    login(client, data.owner)
    url = f"/sessions/{data.session.id}/turns/stream"
    rejected = await client.post(
        url, json=dict(request_id=str(uuid4()), content="한🙂{x}" * 500)
    )
    assert rejected.status_code == 422
    assert "FIXED-INSTRUCTION" not in rejected.text
    shorter = await client.post(
        url, json=dict(request_id=str(uuid4()), content="x")
    )
    if large_instruction:
        assert shorter.status_code == 422
        student.responses.create.assert_not_awaited()
    else:
        assert frames(shorter)[-1][0] == "output.completed"
        assert student.responses.create.await_count == 1
        assert student.responses.create.call_args.kwargs["input"] == [
            {"role": "user", "content": "x"}
        ]
    async with data.factory() as db:
        from src.models import Session

        session = await db.get(Session, data.session.id)
        assert session.config_snapshot_json == original_snapshot
        assert session.config_hash == original_hash


@pytest.mark.parametrize("stream", [True, False])
async def test_initial_ledger_failure_blocks_provider_and_releases_call(
    data, client, student, stream
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import OperationalError

    from src.services.call_admission import active_calls, registered_calls
    from src.services.invocation_types import InvocationError
    from src.services.session_mgr import SessionManager

    def reject(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO api_usage_log"):
            raise OperationalError(
                statement, parameters, Exception("Injected ledger failure")
            )

    sql_event.listen(data.engine.sync_engine, "before_cursor_execute", reject)
    manager = SessionManager(data.db, data.session.id)
    try:
        if stream:
            login(client, data.owner)
            result = await client.post(
                f"/sessions/{data.session.id}/turns/stream",
                json=dict(request_id=str(uuid4()), content="Why?"),
            )
            assert frames(result)[-1][0] == "run.failed"
            assert frames(result)[-1][1]["code"] == "configuration_unavailable"
        else:
            with pytest.raises(
                InvocationError, match="configuration_unavailable"
            ):
                await manager.process_teacher_message("Why?")
    finally:
        sql_event.remove(
            data.engine.sync_engine, "before_cursor_execute", reject
        )
        await manager.close()
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        assert (await db.scalars(select(ApiUsageLog))).all() == []
        teacher = (await db.scalars(select(Message))).one()
        assert teacher.role == "teacher" and teacher.content == "Why?"


async def test_nonstream_teacher_save_failure_releases_prepared_call(
    data, student
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import OperationalError

    from src.services.call_admission import active_calls, registered_calls
    from src.services.session_mgr import SessionManager

    def reject(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO message"):
            raise OperationalError(
                statement,
                parameters,
                Exception("Injected teacher save failure"),
            )

    sql_event.listen(data.engine.sync_engine, "before_cursor_execute", reject)
    manager = SessionManager(data.db, data.session.id)
    try:
        with pytest.raises(OperationalError):
            await manager.process_teacher_message("Why?")
    finally:
        sql_event.remove(
            data.engine.sync_engine, "before_cursor_execute", reject
        )
        await manager.close()
        await data.db.rollback()
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        for model in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(model))).all() == []

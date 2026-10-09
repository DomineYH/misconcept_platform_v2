"""Frozen student execution at HTTP/SQLite and external provider boundaries."""

import json
from uuid import uuid4

import pytest
from lesson_fixtures import install_snapshot
from test_scenario_api import login
from test_student_generation import client, frames, scenario_payload, student

__all__ = ["client", "scenario_payload", "student"]


async def test_student_stream_uses_literal_snapshot_without_legacy_templates(
    data, client, student
):
    await install_snapshot(data, student.connection, student.model)
    from src.services.lesson_snapshots import canonical_hash

    envelope = json.loads(json.dumps(data.session.config_snapshot_json))
    envelope["config"]["student"][
        "behavior_instruction"
    ] = 'Ask freely {x} {{literal}} {"answer":1}'
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    data.scenario.student_template_id = None
    data.scenario.chat_model = "unregistered-current-model"
    data.scenario.problem_situation = None
    await data.db.commit()
    login(client, data.owner)
    result = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json=dict(request_id=str(uuid4()), content="Why?"),
    )
    assert result.status_code == 200, result.text
    assert frames(result)[-1][0] == "output.completed"
    payload = student.responses.create.call_args.kwargs
    assert payload["model"] == "gpt-5-mini"
    assert payload["max_output_tokens"] == 1500
    assert 'Ask freely {x} {{literal}} {"answer":1}' in payload["instructions"]
    for text in (
        "Public problem",
        "Compare fractions",
        "Student profile",
        "Test misconception",
    ):
        assert text in payload["instructions"]
    for text in (
        "PRIVATE ANALYSIS",
        "PRIVATE ANSWER",
        "PRIVATE EVALUATION",
        "숨긴 멘토 지시",
        "항상 존댓말",
        "되묻는 질문을 하지",
    ):
        assert text not in payload["instructions"]
    assert payload["input"] == [{"role": "user", "content": "Why?"}]


async def test_retry_keeps_snapshot_hash_options_and_completed_turn_window(
    data, client, student, monkeypatch
):
    from sqlalchemy import select

    from src.config import config
    from src.models import GenerationRun, Message

    await install_snapshot(
        data, student.connection, student.model, context_turn_limit=2
    )
    for index in range(1, 5):
        for role in ("teacher", "student", "tutor"):
            data.db.add(
                Message(
                    session_id=data.session.id,
                    turn_id=f"prior-{index}",
                    turn_index=index,
                    role=role,
                    content=f"{role} {index}",
                )
            )
    for index in range(1, 5):
        data.db.add(
            GenerationRun(
                id=str(uuid4()),
                owner_id=data.owner.id,
                session_id=data.session.id,
                turn_id=f"prior-{index}",
                operation="student",
                request_id=str(uuid4()),
                input_hash="historic",
                config_hash=data.session.config_hash,
                provider="openai",
                model="gpt-5-mini",
                status="completed",
            )
        )
    data.db.add(
        Message(
            session_id=data.session.id,
            role="student",
            content="Greeting without a completed turn",
        )
    )
    await data.db.commit()
    login(client, data.owner)
    url = f"/sessions/{data.session.id}/turns/stream"
    body = dict(request_id=str(uuid4()), content="Retry {question}")
    successful_events = student.stream.events
    student.stream.events = [RuntimeError("Synthetic provider failure")]
    first = await client.post(url, json=body)
    assert frames(first)[-1][0] == "run.failed"
    turn_id = frames(first)[0][1]["turn_id"]
    original = student.responses.create.call_args.kwargs
    student.stream.events = successful_events
    student.model.default_options_json = {
        "temperature": 0.7
    }  # Invalid for this model; frozen options remain valid.
    student.model.config_version += 1
    data.scenario.prompt = "Changed current instruction"
    data.scenario.chat_model = "changed-model"
    data.scenario.status = "draft"
    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 1)
    monkeypatch.setattr(config, "STUDENT_MAX_TOKENS", 99)
    await data.db.commit()
    retry = await client.post(
        url, json=dict(body, request_id=str(uuid4()), turn_id=turn_id)
    )
    assert frames(retry)[-1][0] == "output.completed", retry.text
    assert student.responses.create.call_args.kwargs == original
    assert original["input"] == [
        {"role": "user", "content": "teacher 3"},
        {"role": "assistant", "content": "student 3"},
        {"role": "user", "content": "teacher 4"},
        {"role": "assistant", "content": "student 4"},
        {"role": "user", "content": "Retry {question}"},
    ]
    async with data.factory() as db:
        runs = (
            await db.scalars(
                select(GenerationRun)
                .where(GenerationRun.turn_id == turn_id)
                .order_by(GenerationRun.started_at)
            )
        ).all()
        assert [(run.status, run.config_hash) for run in runs] == [
            ("failed", data.session.config_hash),
            ("completed", data.session.config_hash),
        ]
    replay = await client.post(url, json=body)
    assert replay.json()["status"] == "failed"
    completed = await client.post(
        url, json=dict(body, request_id=str(uuid4()), turn_id=turn_id)
    )
    assert completed.json()["message"] == frames(retry)[-1][1]["message"]
    assert student.responses.create.await_count == 2


async def test_nonstream_student_reads_snapshot_and_current_access(
    data, student
):
    from types import SimpleNamespace

    from src.services.invocation_types import InvocationError
    from src.services.student_bot import StudentBot

    await install_snapshot(data, student.connection, student.model)
    student.responses.create.return_value = SimpleNamespace(
        output_text="Nonstream answer", usage=None
    )
    async with StudentBot(
        data.db, session_id=data.session.id, owner_id=data.owner.id
    ) as bot:
        content, usage = await bot.generate_response("Why {x}?")
        assert (content, usage) == ("Nonstream answer", None)
        payload = student.responses.create.call_args.kwargs
        assert payload["max_output_tokens"] == 1500
        assert payload["input"] == [{"role": "user", "content": "Why {x}?"}]
        data.scenario.is_active = False
        await data.db.commit()
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await bot.generate_response("Blocked")
    assert student.responses.create.await_count == 1


@pytest.mark.parametrize(
    "damage", ["missing", "malformed", "hash", "schema", "legacy", "version"]
)
async def test_unusable_snapshot_never_falls_back_to_current_student_settings(
    data, client, student, damage
):
    envelope = json.loads(json.dumps(data.session.config_snapshot_json))
    if damage == "missing":
        data.session.config_snapshot_json = None
    elif damage == "malformed":
        data.session.config_snapshot_json = {"config": "broken"}
    elif damage == "hash":
        data.session.config_hash = "0" * 64
    elif damage == "schema":
        envelope["schema_version"] = 2
        data.session.config_snapshot_json = envelope
    elif damage == "legacy":
        data.session.snapshot_origin = "legacy_reconstructed"
    else:
        data.session.source_scenario_version = None
    await data.db.commit()
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json=dict(request_id=str(uuid4()), content="No fallback"),
    )
    assert response.status_code == (409 if damage == "legacy" else 503)
    assert response.json()["detail"]["code"] == (
        "legacy_read_only"
        if damage == "legacy"
        else "configuration_unavailable"
    )
    student.responses.create.assert_not_awaited()
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 400
    assert updates.json()["detail"] == {"code": "configuration_unavailable"}
    from sqlalchemy import select

    from src.models import ApiUsageLog, GenerationRun, Message

    async with data.factory() as db:
        for record in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(record))).all() == []


async def test_session_manager_rejects_legacy_session_before_saving_teacher_message(
    data, student
):
    from fastapi import HTTPException
    from sqlalchemy import select

    from src.models import Message
    from src.services.session_mgr import SessionManager

    data.session.snapshot_origin = "legacy_reconstructed"
    await data.db.commit()
    manager = SessionManager(data.db, data.session.id)
    try:
        with pytest.raises(HTTPException) as rejected:
            await manager.process_teacher_message(
                "Legacy sessions are read only"
            )
        assert rejected.value.status_code == 409
        assert rejected.value.detail["code"] == "legacy_read_only"
        async with data.factory() as db:
            assert (await db.scalars(select(Message))).all() == []
    finally:
        await manager.close()
    student.responses.create.assert_not_awaited()

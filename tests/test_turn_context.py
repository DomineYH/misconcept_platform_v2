"""Generation inputs use bounded pairs, independent of storage order."""

from datetime import datetime
from uuid import uuid4

import pytest
from lesson_fixtures import STUDENT_INSTRUCTION
from sqlalchemy import event
from test_scenario_api import login
from test_student_generation import client, frames, scenario_payload, student

from src.config import config
from src.db.migrations import migrate
from src.models import GenerationRun, Message, Session

__all__ = ["client", "scenario_payload", "student"]
UNFINISHED_TURN = "00000000-0000-0000-0000-000000000061"
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture
async def long_dialogue(data, monkeypatch):
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate.run_all_migrations()
    # Timestamps tie and insertion order opposes turn order deliberately.
    for index in range(60, 0, -1):
        for role in ("student", "tutor", "teacher"):
            data.db.add(
                Message(
                    session_id=data.session.id,
                    turn_id=f"turn-{index}",
                    turn_index=index,
                    role=role,
                    content=f"{role.title()} {index}",
                    created_at=datetime(2026, 1, 1),
                )
            )
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role="student",
                content="Legacy greeting without a turn",
            ),
            Message(
                session_id=data.session.id,
                role="teacher",
                turn_id=UNFINISHED_TURN,
                turn_index=61,
                content="Current question",
            ),
            GenerationRun(
                id=str(uuid4()),
                owner_id=data.owner.id,
                session_id=data.session.id,
                turn_id=UNFINISHED_TURN,
                operation="student",
                request_id=str(uuid4()),
                input_hash="hash",
                config_hash="hash",
                provider="openai",
                model="gpt-5-mini",
                status="failed",
                partial_text="Unsaved student fragment",
            ),
        ]
    )
    other_session = Session(
        scenario_id=data.scenario.id, teacher_id=data.owner.id
    )
    data.db.add(other_session)
    await data.db.flush()
    for role in ("teacher", "student"):
        data.db.add(
            Message(
                session_id=other_session.id,
                role=role,
                turn_id="turn-60",
                turn_index=60,
                content="Other session content",
            )
        )
    await data.db.commit()
    return other_session.id


async def test_student_route_receives_n_completed_pairs_and_current_once(
    data, client, student, long_dialogue, monkeypatch
):
    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 4)
    from lesson_fixtures import install_snapshot

    await install_snapshot(
        data, student.connection, student.model, context_turn_limit=4
    )
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={
            "request_id": str(uuid4()),
            "turn_id": UNFINISHED_TURN,
            "content": "Current question",
        },
    )
    assert frames(response)[-1][0] == "output.completed"
    actual = student.responses.create.call_args.kwargs["input"]
    assert (
        student.responses.create.call_args.kwargs["instructions"]
        == STUDENT_INSTRUCTION
    )
    assert actual == [
        {"role": "user", "content": "Teacher 57"},
        {"role": "assistant", "content": "Student 57"},
        {"role": "user", "content": "Teacher 58"},
        {"role": "assistant", "content": "Student 58"},
        {"role": "user", "content": "Teacher 59"},
        {"role": "assistant", "content": "Student 59"},
        {"role": "user", "content": "Teacher 60"},
        {"role": "assistant", "content": "Student 60"},
        {"role": "user", "content": "Current question"},
    ]
    assert student.responses.create.await_count == 1
    history = await client.get(f"/scenarios/{data.scenario.id}")
    assert history.status_code == 200
    assert history.text.count("data-message-id=") == 183
    assert "Teacher 1" in history.text and "Student 1" in history.text
    assert "Tutor 60" in history.text
    assert "Unsaved student fragment" not in history.text


async def test_mentor_input_stays_at_target_after_later_turns_complete(
    data, long_dialogue, monkeypatch
):
    from src.services.turn_context import load_mentor_context

    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 4)
    context = await load_mentor_context(data.db, data.session.id, "turn-50")
    assert context == [
        {"role": "teacher", "content": "Teacher 46"},
        {"role": "student", "content": "Student 46"},
        {"role": "teacher", "content": "Teacher 47"},
        {"role": "student", "content": "Student 47"},
        {"role": "teacher", "content": "Teacher 48"},
        {"role": "student", "content": "Student 48"},
        {"role": "teacher", "content": "Teacher 49"},
        {"role": "student", "content": "Student 49"},
        {"role": "teacher", "content": "Teacher 50"},
        {"role": "student", "content": "Student 50"},
    ]
    # New paid generation for an older target is forbidden, even without a newer mentor run.
    from types import SimpleNamespace

    from fastapi import HTTPException
    from lesson_fixtures import (
        configure_mentor,
        install_connection,
        install_mentor_model,
        install_snapshot,
    )

    from src.services.mentor_generation import reserve_mentor

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)
    mentor_model = await install_mentor_model(data, connection)
    await configure_mentor(
        data, SimpleNamespace(connection=connection, mentor_model=mentor_model)
    )
    with pytest.raises(HTTPException) as denied:
        await reserve_mentor(
            data.factory,
            data.session.id,
            "turn-50",
            data.owner,
            str(uuid4()),
            "manual",
        )
    assert denied.value.status_code == 409
    assert denied.value.detail == {"code": "mentor_turn_obsolete"}


async def test_nonstream_generation_also_uses_only_completed_pairs(
    data, scenario_payload, long_dialogue, monkeypatch
):
    from src.services.session_mgr import SessionManager

    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 4)
    import httpx2
    from lesson_fixtures import LESSON_KEY, install_connection
    from test_student_probe import sdk_transport

    from src.services.invocation_types import InvocationError

    connection, model = await install_connection(data, monkeypatch)
    from lesson_fixtures import install_snapshot

    await install_snapshot(data, connection, model, context_turn_limit=4)

    async def upstream(request, body):
        return httpx2.Response(
            500,
            json={
                "error": {"code": "server_error", "message": "provider offline"}
            },
        )

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY
    )
    manager = SessionManager(data.db, data.session.id)
    try:
        with pytest.raises(InvocationError, match="transient"):
            await manager.process_teacher_message("Legacy caller question")
        actual = calls[0]["input"]
        assert len(actual) == 9  # Four pairs and the current question.
        assert calls[0]["instructions"] == STUDENT_INSTRUCTION
        assert actual[0] == {"role": "user", "content": "Teacher 57"}
        assert actual[-2:] == [
            {"role": "assistant", "content": "Student 60"},
            {"role": "user", "content": "Legacy caller question"},
        ]
        assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
    finally:
        await manager.close()


@pytest.mark.parametrize(
    "before, first, last", [(None, 51, 60), (50, 40, 49), (1, None, None)]
)
async def test_completed_turn_window_is_limited_and_uses_024_indexes(
    data, long_dialogue, monkeypatch, before, first, last
):
    from src.services.turn_context import load_completed_turns

    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 10)
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "LIMIT" in statement:
            queries.append((statement, parameters))

    event.listen(data.engine.sync_engine, "before_cursor_execute", capture)
    try:
        history = await load_completed_turns(
            data.db, data.session.id, before_turn_index=before
        )
    finally:
        event.remove(data.engine.sync_engine, "before_cursor_execute", capture)
    if first is None:
        assert history == []
    else:
        assert history == [
            {"role": role, "content": f"{role.title()} {index}"}
            for index in range(first, last + 1)
            for role in ("teacher", "student")
        ]
        assert len(history) == 20
    assert len(queries) == 1
    statement, parameters = queries[0]
    assert "turn_index DESC" in statement
    assert "LIMIT ? OFFSET ?" in statement
    connection = await data.db.connection()
    plan = "\n".join(
        row[-1]
        for row in (
            await connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + statement, parameters
            )
        )
    )
    assert "ix_message_completed_student" in plan
    assert "uq_message_turn_role" in plan
    assert "SCAN message" not in plan
    # Only the bounded outer result may sort; the inner LIMIT is indexed.
    assert plan.count("USE TEMP B-TREE FOR ORDER BY") <= 1


@pytest.mark.parametrize("target", [UNFINISHED_TURN, "missing", "turn-50"])
async def test_mentor_context_rejects_unfinished_missing_and_foreign_turns(
    data, long_dialogue, target
):
    from sqlalchemy.exc import NoResultFound

    from src.services.turn_context import load_mentor_context

    session_id = long_dialogue if target == "turn-50" else data.session.id
    with pytest.raises(NoResultFound):
        await load_mentor_context(data.db, session_id, target)


async def test_first_mentor_turn_has_no_invented_prior_context(
    data, long_dialogue
):
    from src.services.turn_context import load_mentor_context

    assert await load_mentor_context(data.db, data.session.id, "turn-1") == [
        {"role": "teacher", "content": "Teacher 1"},
        {"role": "student", "content": "Student 1"},
    ]

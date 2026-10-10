import json

import httpx2 as httpx
import pytest
from lesson_fixtures import install_connection, install_snapshot
from test_analysis_invocations import analysis_transport
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_probe import response_body

from src.models import Message
from src.services import analysis_pipeline
from src.services.analyzer import Analyzer
from src.services.session_synthesizer import SessionSynthesizer
from src.services.student_bot import StudentBot

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
scenario_payload = scenario_fixture
client = client_fixture


async def test_student_success_settings_and_input_failure(data, monkeypatch):
    from lesson_fixtures import LESSON_KEY, install_connection
    from test_student_probe import response_body, sdk_transport

    from src.services.invocation_types import InvocationError

    connection, model = await install_connection(data, monkeypatch)

    async def upstream(request, body):
        return httpx.Response(200, json=response_body("Student answer", USAGE))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1234, key=LESSON_KEY
    )
    await install_snapshot(
        data,
        connection,
        model,
        options={"max_output_tokens": 1234, "reasoning": {"effort": "low"}},
    )
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role="teacher",
                turn_id="prior",
                turn_index=1,
                content="Earlier",
            ),
            Message(
                session_id=data.session.id,
                role="student",
                turn_id="prior",
                turn_index=1,
                content="Answer",
            ),
        ]
    )
    await data.db.commit()
    async with StudentBot(
        data.db, session_id=data.session.id, owner_id=data.owner.id
    ) as bot:
        content, usage = await bot.generate_response("Why?")
        assert content == "Student answer"
        assert usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        kwargs = calls[0]
        assert (
            kwargs["model"],
            kwargs["reasoning"],
            kwargs["max_output_tokens"],
        ) == ("gpt-5-mini", {"effort": "low"}, 1234)
        assert kwargs["input"][-3:] == [
            {"role": "user", "content": "Earlier"},
            {"role": "assistant", "content": "Answer"},
            {"role": "user", "content": "Why?"},
        ]
        data.session.config_hash = "0" * 64
        await data.db.commit()
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await bot.generate_response("Why?")
        assert len(calls) == 1
    assert all(sdk.is_closed() for sdk in clients)


@pytest.mark.parametrize("with_mentor", [False, True])
async def test_session_manager_uses_native_student_without_per_turn_analysis(
    data, scenario_payload, monkeypatch, with_mentor
):
    from lesson_fixtures import LESSON_KEY
    from sqlalchemy import select
    from test_student_probe import sdk_transport

    from src.config import config
    from src.models import ApiUsageLog
    from src.services.session_mgr import SessionManager

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    if with_mentor:
        from types import SimpleNamespace

        from lesson_fixtures import configure_mentor, install_mentor_model

        mentor_model = await install_mentor_model(data, connection)
        await configure_mentor(
            data,
            SimpleNamespace(connection=connection, mentor_model=mentor_model),
        )

    async def upstream(request, body):
        content = "Student answer" if len(calls) == 1 else "Mentor coaching"
        return httpx.Response(200, json=response_body(content, USAGE))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY
    )
    manager = SessionManager(data.db, data.session.id)
    try:
        messages = await manager.process_teacher_message("Why?")
        expected = [
            ("teacher", "Why?"),
            ("student", "Student answer"),
        ]
        assert [(m.role, m.content) for m in messages] == expected
        assert messages[1].analysis_metadata is None
        async with data.factory() as db:
            attempts = (await db.scalars(select(ApiUsageLog))).all()
        assert len(attempts) == len(calls) == 1
        assert [a.operation for a in attempts] == ["student"]
        assert all(a.session_id == data.session.id for a in attempts)
    finally:
        await manager.close()
    assert all(sdk.is_closed() for sdk in clients)


async def test_explicit_mentor_retry_keeps_state_and_one_attempt_per_invocation(
    data, client, monkeypatch
):
    from types import SimpleNamespace
    from uuid import uuid4

    from lesson_fixtures import (
        LESSON_KEY,
        configure_mentor,
        install_mentor_model,
        mentor_output,
    )
    from test_student_generation import frames
    from test_student_probe import sdk_transport

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)
    mentor_model = await install_mentor_model(data, connection)
    await configure_mentor(
        data, SimpleNamespace(connection=connection, mentor_model=mentor_model)
    )
    turn_id = str(uuid4())
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role=role,
                content=content,
                turn_id=turn_id,
                turn_index=1,
            )
            for role, content in [("teacher", "Why?"), ("student", "Answer")]
        ]
    )
    await data.db.commit()

    async def upstream(request, body):
        if len(calls) == 1:
            raise httpx.ConnectError(
                "SECRET connection failure", request=request
            )
        return httpx.Response(
            200, json=response_body(mentor_output("Feedback"), USAGE)
        )

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY, model="gpt-5.2"
    )
    login(client, data.owner)
    url = f"/sessions/{data.session.id}/turns/{turn_id}/mentor/stream"
    failed = await client.post(
        url, json={"request_id": str(uuid4()), "trigger": "manual"}
    )
    assert frames(failed)[-1][1]["code"] == "transient"
    await data.db.refresh(data.session)
    assert len(calls) == 1 and data.session.tutor_intervention_count == 0
    completed = await client.post(
        url, json={"request_id": str(uuid4()), "trigger": "manual"}
    )
    assert frames(completed)[-1][1]["message"]["content"] == "Feedback"
    await data.db.refresh(data.session)
    assert (
        data.session.tutor_intervention_count == 1
        and data.session.tutor_question_count == 1
    )
    assert len(calls) == 2 and all(sdk.is_closed() for sdk in clients)


async def test_classification_and_synthesis_parse_errors_do_not_retry(
    data, monkeypatch
):
    from analysis_fixtures import install_analysis_snapshot
    from sqlalchemy import select

    from src.models import ApiUsageLog
    from src.services.invocation_types import InvocationError
    from src.services.lesson_snapshots import read_lesson_snapshot

    await install_analysis_snapshot(data, monkeypatch)
    snapshot = read_lesson_snapshot(data.session)
    contents = iter(
        [
            '{"label":"A","confidence":0.8,"reasoning":"because"}',
            "not json",
            "not json",
        ]
    )

    async def upstream(request, body):
        return httpx.Response(200, json=response_body(next(contents), USAGE))

    clients, calls = analysis_transport(monkeypatch, upstream)
    analyzer = Analyzer(
        data.factory,
        selection=snapshot.config.analysis.resolved_model_config,
        session_id=data.session.id,
        owner_id=data.owner.id,
    )
    result = await analyzer.classify_question("Why?", snapshot.config.analysis)
    assert (
        result["label"] == "A" and result["reasoning"]["summary"] == "because"
    )
    assert result["_api_usage"]["total_tokens"] == 15
    assert calls[0]["max_output_tokens"] == 1500
    with pytest.raises(InvocationError, match="invalid_json"):
        await analyzer.classify_question("Why?", snapshot.config.analysis)
    assert len(calls) == 2
    synth = SessionSynthesizer(
        data.factory,
        selection=snapshot.config.analysis.resolved_model_config,
        session_id=data.session.id,
        owner_id=data.owner.id,
    )
    _, status = await synth.synthesize(
        messages=[], analysis=snapshot.config.analysis
    )
    assert status == "failed"
    async with data.factory() as db:
        row = await db.scalar(
            select(ApiUsageLog).where(ApiUsageLog.operation == "synthesis")
        )
    assert row.total_tokens == 15
    assert row.status == "failed" and row.error_code == "invalid_json"
    assert len(calls) == 3 and calls[-1]["max_output_tokens"] == 1500
    assert all(sdk.is_closed() for sdk in clients)


async def test_pipeline_with_injected_client_preserves_usage_and_formats(
    data, monkeypatch
):
    from analysis_fixtures import install_analysis_snapshot
    from sqlalchemy import select

    from src.models import ApiUsageLog
    from src.services.lesson_snapshots import read_lesson_snapshot

    await install_analysis_snapshot(data, monkeypatch)
    snapshot = read_lesson_snapshot(data.session)
    teacher = Message(
        id=100, session_id=data.session.id, role="teacher", content="Why?"
    )
    payload = {
        "brief_feedback": ["Good question"],
        "strengths": [{"message_id": 100, "quote": "Why?"}],
        "improvements": [],
        "dialogue_coaching": [],
    }
    from s4_analysis_fixtures import analysis_reply

    payload = analysis_reply([dict(id=100, role="teacher", content="Why?")])
    contents = iter([json.dumps(payload)])

    async def upstream(request, body):
        return httpx.Response(200, json=response_body(next(contents), USAGE))

    clients, calls = analysis_transport(monkeypatch, upstream)
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id,
        [teacher],
        [teacher],
        snapshot,
        data.factory,
        data.owner.id,
    )
    (
        distribution,
        questions,
        report,
        status,
        model,
        prompt_hash,
        pending_usage,
    ) = result
    assert distribution == {"A": 1, "B": 0}
    assert questions[0].message_id == 100
    assert report["brief_feedback"] == ["Good question"] and status == "ok"
    assert len(prompt_hash) == 64
    async with data.factory() as db:
        usage = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert [row.operation for row in usage] == ["analysis_unified"]
    assert all(row.total_tokens == 15 for row in usage)
    assert (
        pending_usage == []
    )  # Common boundary already committed these attempts.
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)


async def test_pipeline_failure_closes_owned_clients(data, monkeypatch):

    from analysis_fixtures import install_analysis_snapshot

    from src.services.lesson_snapshots import read_lesson_snapshot

    await install_analysis_snapshot(data, monkeypatch)
    snapshot = read_lesson_snapshot(data.session)

    async def upstream(request, body):
        raise httpx.ConnectError(
            "Synthetic connection failure", request=request
        )

    created, calls = analysis_transport(monkeypatch, upstream)
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id,
        [Message(id=100, role="teacher", content="Why?")],
        [Message(id=100, role="teacher", content="Why?")],
        snapshot,
        data.factory,
        data.owner.id,
    )
    assert result[3] == "failed"
    assert len(created) == len(calls) == 2
    assert all(sdk.is_closed() for sdk in created)


async def test_message_route_closes_clients_on_bot_failure(data, monkeypatch):
    from lesson_fixtures import LESSON_KEY, install_connection
    from test_regressions import request
    from test_student_probe import sdk_transport

    from src.api.routes.student_generation import StudentRequest, student_turn

    connection, model = await install_connection(data, monkeypatch)
    await install_snapshot(data, connection, model)
    sid = data.session.id
    data.scenario.problem_situation = "Public problem"
    data.session.ended_at = None
    await data.db.commit()

    async def upstream(request, body):
        return httpx.Response(500, json={"error": {"code": "server_error"}})

    created, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY
    )
    result = await student_turn(
        request(),
        sid,
        StudentRequest(
            request_id="00000000-0000-0000-0000-000000000001", content="Why?"
        ),
        data.owner,
        data.db,
    )
    body = "".join([chunk async for chunk in result.body_iterator])
    assert "event: run.failed" in body
    assert "event: output.completed" not in body
    assert all(sdk.is_closed() for sdk in created)
    assert len(created) == len(calls) == 1

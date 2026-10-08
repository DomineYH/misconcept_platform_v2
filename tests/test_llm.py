import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2 as httpx
import pytest
from lesson_fixtures import install_connection
from openai import APIConnectionError, AsyncOpenAI
from tenacity import wait_none
from test_analysis_invocations import analysis_transport
from test_student_probe import response_body

from src.models import Message
from src.services import analysis_pipeline, base
from src.services.analyzer import Analyzer
from src.services.prompt_manager import PromptManager
from src.services.session_synthesizer import SessionSynthesizer
from src.services.student_bot import StudentBot
from src.services.tutor_bot import TutorBot

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def response(content):
    return SimpleNamespace(output_text=content, usage=SimpleNamespace(**USAGE))


def client(*responses):
    return SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(side_effect=list(responses))
        ),
        close=AsyncMock(),
        max_retries=0,
    )


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(
        base.OpenAIBaseService.create_response.retry, "wait", wait_none()
    )
    monkeypatch.setattr(
        PromptManager,
        "get_template_text_by_id",
        AsyncMock(
            return_value="{scenario_title}: {prompt} / {student_profile}"
        ),
    )


async def test_student_success_settings_and_input_failure(data, monkeypatch):
    from lesson_fixtures import LESSON_KEY, install_connection
    from test_student_probe import response_body, sdk_transport

    from src.services.invocation_types import InvocationError

    await install_connection(data, monkeypatch)

    async def upstream(request, body):
        return httpx.Response(200, json=response_body("Student answer", USAGE))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1234, key=LESSON_KEY
    )
    async with StudentBot(
        "Misconception",
        "Scenario",
        "Profile",
        data.db,
        1,
        model="gpt-5-mini",
        reasoning_effort="low",
        max_tokens=1234,
    ) as bot:
        content, usage = await bot.generate_response(
            "Why?",
            [
                {"role": "teacher", "content": "Earlier"},
                {"role": "student", "content": "Answer"},
            ],
        )
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
        PromptManager.get_template_text_by_id.side_effect = ValueError(
            "Invalid template"
        )
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await bot.generate_response("Why?", [])
        assert len(calls) == 1
    assert all(sdk.is_closed() for sdk in clients)


@pytest.mark.parametrize(
    "status, attempts",
    [
        (429, 3),
        (500, 3),
        (408, 3),
        (409, 3),
        (400, 1),
        (401, 1),
        (403, 1),
        (422, 1),
    ],
)
async def test_real_sdk_transport_attempt_count_and_owned_close(
    status, attempts, monkeypatch
):
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "test", "type": "test"}}
        )

    owned = AsyncOpenAI(
        api_key="fake",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return owned

    monkeypatch.setattr(base, "AsyncOpenAI", factory)
    with pytest.raises(Exception):
        async with base.OpenAIBaseService() as service:
            await service.create_response(model="gpt-5-mini", input="test")
    assert len(calls) == attempts
    assert owned.is_closed()


async def test_explicit_tutor_retry_keeps_state_and_one_attempt_per_invocation(
    data, monkeypatch
):
    from lesson_fixtures import (
        LESSON_KEY,
        install_connection,
        install_mentor_model,
    )
    from test_student_probe import response_body, sdk_transport

    from src.services.invocation_types import InvocationError

    connection, _ = await install_connection(data, monkeypatch)
    await install_mentor_model(data, connection)

    async def upstream(request, body):
        if len(calls) == 1:
            raise httpx.ConnectError(
                "SECRET connection failure", request=request
            )
        return httpx.Response(200, json=response_body("Feedback", USAGE))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY, model="gpt-5.2"
    )
    async with TutorBot(
        data.db, 1, sensitivity="high", initial_question_count=1
    ) as bot:
        with pytest.raises(InvocationError, match="transient"):
            await bot.generate_feedback(
                "Why?", "Answer", [], question_counted=True
            )
        assert len(calls) == 1 and bot.intervention_count == 0
        content, _ = await bot.generate_feedback(
            "Why?", "Answer", [], question_counted=True
        )
        assert content == "Feedback" and bot.intervention_count == 1
        assert bot.question_count == 1
    assert len(calls) == 2 and all(sdk.is_closed() for sdk in clients)


async def test_classification_and_synthesis_parse_errors_do_not_retry(
    data, monkeypatch
):
    from sqlalchemy import select

    from src.config import config
    from src.models import ApiUsageLog
    from src.services.invocation_types import InvocationError

    _, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": dict(model.verification_state["student"]),
    }
    monkeypatch.setattr(config, "ANALYSIS_MODEL", model.model_id)
    await data.db.commit()
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
    analyzer = Analyzer(data.factory)
    result = await analyzer.classify_question("Why?", data.framework)
    assert (
        result["label"] == "A" and result["reasoning"]["summary"] == "because"
    )
    assert result["_api_usage"]["total_tokens"] == 15
    assert calls[0]["max_output_tokens"] == 1500
    with pytest.raises(InvocationError, match="invalid_json"):
        await analyzer.classify_question("Why?", data.framework)
    assert len(calls) == 2
    synth = SessionSynthesizer(data.factory)
    _, status = await synth.synthesize(messages=[], framework=data.framework)
    assert status == "failed"
    async with data.factory() as db:
        row = await db.scalar(
            select(ApiUsageLog).where(ApiUsageLog.operation == "synthesis")
        )
    assert row.total_tokens == 15
    assert row.status == "failed" and row.error_code == "invalid_json"
    assert len(calls) == 3 and calls[-1]["max_output_tokens"] == 2500
    assert all(sdk.is_closed() for sdk in clients)


async def test_pipeline_with_injected_client_preserves_usage_and_formats(
    data, monkeypatch
):
    from sqlalchemy import select

    from src.config import config
    from src.models import ApiUsageLog

    _, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": dict(model.verification_state["student"]),
    }
    monkeypatch.setattr(config, "ANALYSIS_MODEL", model.model_id)
    await data.db.commit()
    teacher = Message(
        id=100, session_id=data.session.id, role="teacher", content="Why?"
    )
    payload = {
        "brief_feedback": ["Good question"],
        "strengths": [{"message_id": 100, "quote": "Why?"}],
        "improvements": [],
        "dialogue_coaching": [],
    }
    contents = iter(
        [
            '{"results":[{"index":0,"is_greeting":false}]}',
            '{"label":"A","confidence":0.9}',
            json.dumps(payload),
        ]
    )

    async def upstream(request, body):
        return httpx.Response(200, json=response_body(next(contents), USAGE))

    clients, calls = analysis_transport(monkeypatch, upstream)
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id,
        [teacher],
        [teacher],
        data.scenario,
        data.framework,
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
    assert [row.operation for row in usage] == [
        "greeting",
        "classification",
        "synthesis",
    ]
    assert all(row.total_tokens == 15 for row in usage)
    assert (
        pending_usage == []
    )  # Common boundary already committed these attempts.
    assert len(calls) == 3 and all(sdk.is_closed() for sdk in clients)


async def test_pipeline_failure_closes_owned_clients(data, monkeypatch):
    from src.config import config

    _, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": dict(model.verification_state["student"]),
    }
    monkeypatch.setattr(config, "ANALYSIS_MODEL", model.model_id)
    await data.db.commit()

    async def upstream(request, body):
        raise httpx.ConnectError(
            "Synthetic connection failure", request=request
        )

    created, calls = analysis_transport(monkeypatch, upstream)
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id,
        [],
        [],
        data.scenario,
        data.framework,
        data.factory,
    )
    assert result[3] == "failed"
    assert len(created) == len(calls) == 2
    assert all(sdk.is_closed() for sdk in created)


async def test_message_route_closes_clients_on_bot_failure(data, monkeypatch):
    from lesson_fixtures import LESSON_KEY, install_connection
    from test_regressions import request
    from test_student_probe import sdk_transport

    from src.api.routes.student_generation import StudentRequest, student_turn

    await install_connection(data, monkeypatch)
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


async def test_injected_sdk_retry_policy_is_explicit():
    async with AsyncOpenAI(api_key="fake", max_retries=2) as sdk:
        with pytest.raises(ValueError, match="max_retries=0"):
            base.OpenAIBaseService(client=sdk)


async def test_owned_client_closes_after_success(monkeypatch):
    fake = client(response("ok"))
    monkeypatch.setattr(base, "AsyncOpenAI", lambda **kwargs: fake)
    async with base.OpenAIBaseService() as service:
        assert (
            await service.create_response(model="gpt-5-mini", input="test")
        ).output_text == "ok"
    await service.close()
    fake.close.assert_awaited_once()

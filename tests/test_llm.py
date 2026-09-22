import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI, APIConnectionError
from tenacity import wait_none

from src.services import base, analysis_pipeline
from src.services.analyzer import Analyzer
from src.services.session_synthesizer import SessionSynthesizer
from src.services.student_bot import StudentBot
from src.services.tutor_bot import TutorBot
from src.services.prompt_manager import PromptManager
from src.models import Message

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


async def test_student_success_settings_and_input_failure(data):
    fake = client(response("Student answer"))
    async with StudentBot(
        "Misconception",
        "Scenario",
        "Profile",
        data.db,
        1,
        model="gpt-5-mini",
        reasoning_effort="low",
        max_tokens=1234,
        client=fake,
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
        kwargs = fake.responses.create.call_args.kwargs
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
        with pytest.raises(RuntimeError, match="Invalid template"):
            await bot.generate_response("Why?", [])
        assert fake.responses.create.await_count == 1
    fake.close.assert_not_awaited()


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


async def test_retry_success_does_not_repeat_tutor_state(data):
    error = APIConnectionError(
        request=httpx.Request("POST", "https://example.test")
    )
    fake = client(error, response("Feedback"))
    bot = TutorBot(data.db, 1, client=fake)
    bot.analyze_conversation = AsyncMock(return_value=(True, "low_leverage"))
    content, _ = await bot.generate_feedback("Question", "Answer", [])
    assert content == "Feedback" and bot.intervention_count == 1
    assert fake.responses.create.await_count == 2
    bot.analyze_conversation.assert_awaited_once()


async def test_classification_and_synthesis_parse_errors_do_not_retry(data):
    fake = client(
        response('{"label":"A","confidence":0.8,"reasoning":"because"}'),
        response("not json"),
    )
    analyzer = Analyzer(client=fake)
    result = await analyzer.classify_question("Why?", data.framework)
    assert (
        result["label"] == "A" and result["reasoning"]["summary"] == "because"
    )
    assert result["_api_usage"]["total_tokens"] == 15
    assert fake.responses.create.call_args.kwargs["max_output_tokens"] == 1500
    with pytest.raises(ValueError, match="Invalid JSON"):
        await analyzer.classify_question("Why?", data.framework)
    assert fake.responses.create.await_count == 2
    fake = client(response("not json"))
    synth = SessionSynthesizer(client=fake)
    _, status = await synth.synthesize(messages=[], framework=data.framework)
    assert status == "failed"
    assert synth.last_usage["total_tokens"] == 15
    assert fake.responses.create.await_count == 1
    assert fake.responses.create.call_args.kwargs["max_output_tokens"] == 2500


async def test_pipeline_with_injected_client_preserves_usage_and_formats(data):
    teacher = Message(
        id=100, session_id=data.session.id, role="teacher", content="Why?"
    )
    payload = {
        "brief_feedback": ["Good question"],
        "strengths": [{"message_id": 100, "quote": "Why?"}],
        "improvements": [],
        "dialogue_coaching": [],
    }
    fake = client(
        response('[{"index":0,"is_greeting":false}]'),
        response('{"label":"A","confidence":0.9}'),
        response(json.dumps(payload)),
    )
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id,
        [teacher],
        [teacher],
        data.scenario,
        data.framework,
        client=fake,
    )
    distribution, questions, report, status, model, prompt_hash, usage = result
    assert distribution == {"A": 1, "B": 0}
    assert questions[0].message_id == 100
    assert report["brief_feedback"] == ["Good question"] and status == "ok"
    assert len(prompt_hash) == 64
    assert [row.operation for row in usage] == [
        "greeting",
        "classification",
        "synthesis",
    ]
    assert all(row.total_tokens == 15 for row in usage)
    fake.close.assert_not_awaited()


async def test_pipeline_failure_closes_owned_clients(data, monkeypatch):
    created = []

    def factory(**kwargs):
        fake = client(
            APIConnectionError(
                request=httpx.Request("POST", "https://example.test")
            ),
            APIConnectionError(
                request=httpx.Request("POST", "https://example.test")
            ),
            APIConnectionError(
                request=httpx.Request("POST", "https://example.test")
            ),
        )
        created.append(fake)
        return fake

    monkeypatch.setattr(base, "AsyncOpenAI", factory)
    result = await analysis_pipeline.run_llm_pipeline(
        data.session.id, [], [], data.scenario, data.framework
    )
    assert result[3] == "failed"
    assert len(created) == 2
    assert created[1].responses.create.await_count == 3
    for fake in created:
        fake.close.assert_awaited_once()


async def test_message_route_closes_clients_on_bot_failure(data, monkeypatch):
    from src.api.routes.session_messages import send_message
    from test_regressions import request

    sid = data.session.id
    data.session.ended_at = None
    await data.db.commit()
    created = []

    def factory(**kwargs):
        fake = client(ValueError("invalid response"))
        created.append(fake)
        return fake

    monkeypatch.setattr(base, "AsyncOpenAI", factory)
    with pytest.raises(RuntimeError):
        await send_message(request(), sid, "Why?", data.owner, data.db)
    for fake in created:
        fake.close.assert_awaited_once()
    assert len(created) == 2  # Student and misconception; tutor is disabled.


async def test_injected_sdk_retry_policy_is_explicit():
    async with AsyncOpenAI(api_key="fake", max_retries=2) as sdk:
        with pytest.raises(ValueError, match="max_retries=0"):
            Analyzer(client=sdk)


async def test_owned_client_closes_after_success(monkeypatch):
    fake = client(response('ok'))
    monkeypatch.setattr(base, 'AsyncOpenAI', lambda **kwargs: fake)
    async with base.OpenAIBaseService() as service:
        assert (await service.create_response(model='gpt-5-mini', input='test')).output_text == 'ok'
    await service.close()
    fake.close.assert_awaited_once()

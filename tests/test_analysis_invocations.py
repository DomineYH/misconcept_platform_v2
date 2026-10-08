"""Runtime analysis through authenticated HTTP, WAL SQLite and SDK transport."""

import json

import httpx2
import pytest
from lesson_fixtures import LESSON_KEY, install_connection
from openai import AsyncOpenAI
from sqlalchemy import select
from test_provider_connections import api as provider_api
from test_scenario_api import login
from test_student_probe import response_body

from src.config import config
from src.models import ApiUsageLog, Message

connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


@pytest.fixture
async def api(connection_api, data):
    login(connection_api, data.owner)
    await connection_api.get("/health")
    yield connection_api


def analysis_transport(monkeypatch, handler):
    from src.services import openai_generation

    clients, calls = [], []

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {LESSON_KEY}"
        body = json.loads(request.content)
        assert body["model"] == config.ANALYSIS_MODEL and body["store"] is False
        calls.append(body)
        return await handler(request, body)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"].connect == 5
        sdk = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )
        clients.append(sdk)
        return sdk

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    return clients, calls


async def prepare_analysis(data, monkeypatch):
    _, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": dict(model.verification_state["student"]),
    }
    monkeypatch.setattr(config, "ANALYSIS_MODEL", model.model_id)
    data.db.add(
        Message(session_id=data.session.id, role="teacher", content="Why?")
    )
    await data.db.commit()


def result_for(body):
    name = body["text"]["format"]["name"]
    if name == "RuntimeGreetings":
        return {"results": [{"index": 0, "is_greeting": False}]}
    if name == "RuntimeClassification":
        return {"label": "A", "confidence": 0.9, "reasoning": "because"}
    return {
        "brief_feedback": ["Good question"],
        "strengths": [],
        "improvements": [],
        "dialogue_coaching": [],
    }


async def test_analysis_uses_db_key_and_ledger_and_preserves_degraded(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        assert body["text"]["format"]["strict"] is True
        assert body["reasoning"] == {"effort": config.ANALYSIS_REASONING}
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    # Greeting/classification/synthesis preserve their distinct legacy budgets.
    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    assert response.json()["feedback_status"] == "degraded"
    assert len(calls) == 3 and all(client.is_closed() for client in clients)
    assert [c["max_output_tokens"] for c in calls] == [500, 1500, 2500]
    async with data.factory() as db:
        rows = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert [(r.operation, r.attempt_no, r.status) for r in rows] == [
        ("greeting", 1, "completed"),
        ("classification", 1, "completed"),
        ("synthesis", 1, "completed"),
    ]
    assert all(
        r.total_tokens == 15 and r.session_id == data.session.id for r in rows
    )
    assert all(r.owner_id == data.owner.id and r.run_id is None for r in rows)


async def test_runtime_preserves_repairs_and_nullable_degraded_feedback(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        name = body["text"]["format"]["name"]
        value = result_for(body)
        if name == "RuntimeClassification":
            value = {
                "label": "unknown",
                "confidence": 9,
                "reasoning": {"summary": "legacy", "pedagogical": "discarded"},
            }
        elif name == "RuntimeSynthesis":
            value = {
                "brief_feedback": ["가" * 80],
                "strengths": [{"message_id": 999, "quote": "missing"}],
                "improvements": None,
                "dialogue_coaching": None,
            }
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.json()["feedback_status"] == "degraded"
    assert response.json()["feedback"] == "가" * 70 + "…"
    reader = await api.get(f"/sessions/{data.session.id}/analysis")
    question = reader.json()["questions"][0]
    assert question["label"] == "A" and question["confidence"] == 1.0
    assert question["reasoning"] == {
        "summary": "legacy",
        "improved_sentence": None,
    }
    assert len(calls) == 3 and all(c.is_closed() for c in clients)


@pytest.mark.parametrize(
    "blocked", ["missing", "unverified", "disabled", "options"]
)
async def test_unavailable_analysis_configuration_never_calls_provider_or_environment_key(
    data, api, monkeypatch, blocked
):
    await prepare_analysis(data, monkeypatch)
    from src.models import ModelConfig

    model = await data.db.scalar(select(ModelConfig))
    if blocked == "missing":
        monkeypatch.setattr(config, "ANALYSIS_MODEL", "missing-exact-model")
    elif blocked == "unverified":
        model.verification_state = {"analysis": {"status": "unverified"}}
    elif blocked == "disabled":
        model.enabled = False
    else:
        monkeypatch.setattr(config, "ANALYSIS_REASONING", "unsupported-effort")
    await data.db.commit()

    async def upstream(request, body):
        raise AssertionError("Configuration must block this request")

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.json()["feedback_status"] == "failed"
    assert calls == clients == []
    async with data.factory() as db:
        assert (await db.scalars(select(ApiUsageLog))).all() == []

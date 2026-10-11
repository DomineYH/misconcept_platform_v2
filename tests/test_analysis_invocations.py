"""Runtime analysis through authenticated HTTP, WAL SQLite and SDK transport."""

import json

import httpx2
import pytest
from analysis_fixtures import install_analysis_snapshot
from lesson_fixtures import LESSON_KEY
from openai import AsyncOpenAI
from s4_analysis_fixtures import analysis_reply, prompt_inputs
from sqlalchemy import select
from test_provider_connections import api as provider_api
from test_scenario_api import login
from test_student_probe import response_body

from src.models import ApiUsageLog, Message

connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


@pytest.fixture
async def api(connection_api, data):
    login(connection_api, data.owner)
    await connection_api.get("/health")
    from analysis_test_helpers import AnalysisApi

    yield AnalysisApi(connection_api)


def analysis_transport(monkeypatch, handler):
    from src.services import openai_generation

    clients, calls = [], []

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {LESSON_KEY}"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5-mini" and body["store"] is False
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
    await install_analysis_snapshot(data, monkeypatch)
    data.db.add(
        Message(session_id=data.session.id, role="teacher", content="Why?")
    )
    await data.db.commit()


def result_for(body):
    name = body["text"]["format"]["name"]
    if name == "UnifiedAnalysisOutput":
        inputs = prompt_inputs(body)
        return analysis_reply(
            inputs["messages"],
            enabled=inputs["analysis"]["classification_enabled"],
        )
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
        assert body["reasoning"] == {"effort": "medium"}
        value = result_for(body)
        value["message_classifications"][0].update(
            disposition="unclassified", rubric_id=None
        )
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    # Every subcall uses the frozen analysis role options.
    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    assert response.json()["feedback_status"] == "degraded"
    assert len(calls) == 1 and all(client.is_closed() for client in clients)
    assert [c["max_output_tokens"] for c in calls] == [8192]
    async with data.factory() as db:
        rows = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert [(r.operation, r.attempt_no, r.status) for r in rows] == [
        ("analysis_unified", 1, "completed"),
    ]
    assert all(
        r.total_tokens == 15 and r.session_id == data.session.id for r in rows
    )
    assert all(
        r.owner_id == data.owner.id
        and r.run_id == response.json()["latest_run"]["run_id"]
        for r in rows
    )


@pytest.mark.parametrize(
    "operation,feedback_status,error_code",
    [
        ("synthesis", "failed", "empty_response"),
        ("classification", "degraded", "invalid_output"),
    ],
)
async def test_semantically_failed_result_is_a_failed_ledger_attempt(
    data, api, monkeypatch, operation, feedback_status, error_code
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        if (
            operation == "synthesis"
            and body["text"]["format"]["name"] == "UnifiedAnalysisOutput"
        ):
            value["brief_feedback"] = []
        if (
            operation == "classification"
            and body["text"]["format"]["name"] == "UnifiedAnalysisOutput"
        ):
            value["message_classifications"][0]["confidence"] = "invalid-number"
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.json()["feedback_status"] == feedback_status
    async with data.factory() as db:
        row = await db.scalar(
            select(ApiUsageLog).where(
                ApiUsageLog.operation == "analysis_unified"
            )
        )
    assert row.status == "failed" and row.error_code == error_code
    assert row.total_tokens == 15 and row.attempt_no == 1
    assert len(calls) == 1 and all(c.is_closed() for c in clients)


async def test_runtime_rejects_legacy_repairs_and_nullable_envelope(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        value["message_classifications"][0]["confidence"] = 9
        value["improvements"] = None
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.json()["feedback_status"] == "failed"
    reader = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    assert reader["accepted_report"] is None
    assert reader["questions"][0]["label"] == "Unclassified"
    assert reader["questions"][0]["confidence"] is None
    assert len(calls) == 1 and all(c.is_closed() for c in clients)


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
        model.model_id = "changed-identity"
    elif blocked == "unverified":
        model.verification_state = {"analysis": {"status": "unverified"}}
    elif blocked == "disabled":
        model.enabled = False
    else:
        model.capability_definition_version = "unsupported-definition"
    await data.db.commit()

    async def upstream(request, body):
        raise AssertionError("Configuration must block this request")

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "configuration_unavailable"
    assert calls == clients == []
    async with data.factory() as db:
        assert (await db.scalars(select(ApiUsageLog))).all() == []

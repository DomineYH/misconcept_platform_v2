"""Gemini role probes at authenticated HTTP → SQLite → official SDK transport."""

import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from test_google_catalog import install, save_key
from test_google_invocations import response, sse
from test_model_management import write
from test_role_probes import (
    CLASSIFICATION,
    MENTOR_NEGATIVE,
    MENTOR_POSITIVE,
    SYNTHESIS,
)
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import completed

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def prepare(api, role):
    await save_key(api)
    saved = await write(
        api,
        "models",
        provider="google",
        model_id=" models/gemini-2.5-flash ",
        display_name="Gemini",
    )
    assert saved.status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["model_id"] == "gemini-2.5-flash"
    assert model["capabilities"]["definition_version"] == "google-2026-10-09-v1"
    return dict(expected_version=1, role=role, request_id=str(uuid4()))


@pytest.mark.parametrize("role", ["student", "mentor", "analysis"])
async def test_google_role_probe_is_versioned_idempotent_and_has_two_attempts(
    data, api, monkeypatch, role
):
    body = await prepare(api, role)
    budget = {"student": 1024, "mentor": 1500, "analysis": 2500}[role]

    async def upstream(request):
        payload = json.loads(request.content)
        config = payload["generationConfig"]
        assert config["maxOutputTokens"] == budget
        assert (
            "response_format" not in config and "responseFormat" not in config
        )
        async with data.engine.connect() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT provider,status,attempt_no FROM api_usage_log WHERE status='running'"
                    )
                )
            ).one()
            assert row == ("google", "running", 1)
        content = "학생의 생각을 물어보세요."
        if role in ("analysis", "mentor"):
            assert config["responseMimeType"] == "application/json"
            assert config["responseJsonSchema"]["type"] == "object"
            content = json.dumps(
                (MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE)
                if role == "mentor"
                else CLASSIFICATION if len(calls) == 1 else SYNTHESIS
            )
        value = response(
            content,
            usage={
                "promptTokenCount": 10,
                "cachedContentTokenCount": 0,
                "candidatesTokenCount": 4,
                "thoughtsTokenCount": 2,
                "totalTokenCount": 16,
            },
        )
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(value),
            )
        return httpx.Response(200, json=value)

    clients, calls = install(monkeypatch, upstream)
    started = await write(api, "models/1/probes", **body)
    assert started.status_code == 202
    result = await completed(api, body["request_id"])
    assert result["status"] == "succeeded", result
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert len(calls) == 2 and all(c.is_closed for c in clients)
    state = (await api.get("/admin/ai/state")).json()
    model = state["models"][0]
    assert model["probe_budgets"][role] == budget
    evidence = model["verification_state"][role]
    assert (
        evidence["status"] == "succeeded"
        and evidence["credential_revision"] == 1
    )
    assert evidence[
        "capability_definition_version"
    ] == "google-2026-10-09-v1" and evidence["role_contract_version"] == (
        "s3-v1" if role == "mentor" else "s1-v1"
    )
    assert all(
        value["status"] == "unverified"
        for name, value in model["verification_state"].items()
        if name != role
    )
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT provider,role,status,attempt_no,input_tokens,output_tokens,total_tokens,estimated_cost_usd FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert rows == [("google", role, "completed", 1, 10, 6, 16, None)] * 2
    assert "학생의 생각을 물어보세요" not in json.dumps(state) + json.dumps(
        result
    )
    conflict = await write(
        api,
        "models/1/probes",
        **{**body, "role": "mentor" if role != "mentor" else "student"},
    )
    assert conflict.status_code == 409 and len(calls) == 2


@pytest.mark.parametrize(
    "role,mode,code,steps",
    [
        ("mentor", "json", "invalid_json", 1),
        ("mentor", "type", "invalid_output", 1),
        ("analysis", "label", "invalid_reference", 1),
        ("analysis", "quote", "invalid_reference", 2),
        ("analysis", "length", "invalid_output", 2),
        ("analysis", "limit", "output_limit", 1),
    ],
)
async def test_structured_google_probe_uses_server_validation_and_stops_on_failure(
    data, api, monkeypatch, caplog, role, mode, code, steps
):
    from copy import deepcopy

    body = await prepare(api, role)

    async def upstream(request):
        value = deepcopy(
            MENTOR_POSITIVE
            if role == "mentor"
            else CLASSIFICATION if len(calls) == 1 else SYNTHESIS
        )
        if mode == "type":
            value["should_intervene"] = "false"
        if mode == "label":
            value["label"] = "PRIVATE-OUTPUT"
        if mode == "quote" and len(calls) == 2:
            value["strengths"][0]["quote"] = "PRIVATE-OUTPUT"
        if mode == "length" and len(calls) == 2:
            value["improvements"][0]["alternative_question"] = "가" * 61
        content = '{"PRIVATE-OUTPUT":' if mode == "json" else json.dumps(value)
        return httpx.Response(
            200,
            json=response(
                content,
                finish="MAX_TOKENS" if mode == "limit" else "STOP",
                usage={
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 2,
                    "totalTokenCount": 16,
                },
            ),
        )

    clients, calls = install(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    result = await completed(api, body["request_id"])
    assert result["status"] == "failed" and result["error_code"] == code
    assert len(calls) == steps and all(c.is_closed for c in clients)
    state = (await api.get("/admin/ai/state")).json()
    assert (
        "PRIVATE-OUTPUT"
        not in json.dumps(state) + json.dumps(result) + caplog.text
    )
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT status,error_code,total_tokens FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert len(rows) == steps and rows[-1] == ("failed", code, 16)


async def test_google_probe_cancel_closes_response_client_and_slot(
    data, api, monkeypatch
):
    import asyncio

    from src.services.call_admission import active_calls, registered_calls

    body = await prepare(api, "student")
    entered = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield sse(response("", finish=None))
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = Body()

    async def upstream(request):
        if len(calls) == 1:
            return httpx.Response(200, json=response())
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    clients, calls = install(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    await asyncio.wait_for(entered.wait(), 10)
    assert (
        await write(api, f"probes/{body['request_id']}/cancel")
    ).status_code == 200
    result = await completed(api, body["request_id"])
    assert (
        result["status"] == "failed" and result["error_code"] == "interrupted"
    )
    assert stream.closed and all(c.is_closed for c in clients)
    assert not active_calls and not registered_calls
    async with data.engine.connect() as db:
        assert (
            await db.execute(
                text("SELECT status FROM api_usage_log ORDER BY id")
            )
        ).scalars().all() == ["completed", "cancelled"]

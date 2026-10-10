"""Claude role verification through authenticated HTTP and official SDK."""

import json
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import text
from test_anthropic_catalog import MODEL, save_key, sdk_transport
from test_model_management import write
from test_provider_connections import api as provider_api
from test_role_probes import (
    CLASSIFICATION,
    MENTOR_NEGATIVE,
    MENTOR_POSITIVE,
    SYNTHESIS,
)
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import completed, sse

api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def prepare(api):
    assert (await save_key(api)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="anthropic",
            model_id=MODEL,
            display_name="Claude probe",
        )
    ).status_code == 200


async def test_claude_options_keep_native_thinking_and_effort(data, api):
    await prepare(api)
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["capabilities"] is not None
    assert model["capabilities"]["checked_at"] == "2026-10-10"
    assert model["capabilities"]["structured"] is True
    options = {
        "max_output_tokens": 4096,
        "temperature": 1,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "output_config": {"effort": "low"},
    }
    response = await write(
        api,
        "models/1/update",
        expected_version=1,
        display_name="Claude probe",
        enabled=False,
        default_options=options,
    )
    assert response.status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["default_options"] == options
    assert model["probe_budgets"] == {"mentor": 1500, "analysis": 2500}


def response_body(content="합성 학생 응답입니다.", **values):
    body = dict(
        id="msg_claude",
        type="message",
        role="assistant",
        model=MODEL,
        content=[
            {
                "type": "thinking",
                "thinking": "PRIVATE-THINKING",
                "signature": "PRIVATE-SIGNATURE",
            },
            {"type": "text", "text": content},
        ],
        stop_reason="end_turn",
        stop_sequence=None,
        usage={
            "input_tokens": 10,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
            "output_tokens": 8,
            "output_tokens_details": {"thinking_tokens": 5},
        },
    )
    body.update(values)
    return body


def stream_body(
    content="합성 학생 응답입니다.", reason="end_turn", finish=True
):
    message = response_body()
    message.update(content=[], stop_reason=None)
    body = sse("message_start", message=message)
    body += sse(
        "content_block_start",
        index=0,
        content_block={"type": "thinking", "thinking": "", "signature": ""},
    )
    body += sse(
        "content_block_delta",
        index=0,
        delta={"type": "thinking_delta", "thinking": "PRIVATE-THINKING"},
    )
    body += sse(
        "content_block_delta",
        index=0,
        delta={"type": "signature_delta", "signature": "PRIVATE-SIGNATURE"},
    )
    body += sse("content_block_stop", index=0)
    body += sse(
        "content_block_start",
        index=1,
        content_block={"type": "text", "text": ""},
    )
    body += sse(
        "content_block_delta",
        index=1,
        delta={"type": "text_delta", "text": content},
    )
    body += sse("content_block_stop", index=1)
    body += sse(
        "message_delta",
        delta={"stop_reason": reason, "stop_sequence": None},
        usage={"output_tokens": 12},
    )
    if finish:
        body += sse("message_stop")
    return body


async def test_claude_student_probe_uses_two_calls_and_observed_usage(
    data, api, monkeypatch, caplog
):
    await prepare(api)
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["probe_budgets"]["student"] == 1024

    async def upstream(request):
        assert request.method == "POST" and request.url.path == "/v1/messages"
        payload = json.loads(request.content)
        assert payload["model"] == MODEL and payload["max_tokens"] == 1024
        assert (
            "max_output_tokens" not in payload
            and "output_config" not in payload
        )
        assert isinstance(payload["system"], str)
        if payload.get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stream_body(),
            )
        return httpx2.Response(200, json=response_body())

    clients, calls = sdk_transport(
        monkeypatch, upstream, "anthropic_generation"
    )
    body = dict(expected_version=1, role="student", request_id=str(uuid4()))
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT provider,probe_step,status,input_tokens,output_tokens,total_tokens,cache_read_tokens,cache_write_tokens,reasoning_tokens FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert rows == [
            ("anthropic", "text", "completed", 15, 8, 23, 3, 2, 5),
            ("anthropic", "stream", "completed", 15, 12, 27, 3, 2, 5),
        ]
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert state["verification_state"]["student"]["status"] == "succeeded"
    assert state["verification_state"]["mentor"]["status"] == "unverified"
    assert "PRIVATE-" not in json.dumps(state) + caplog.text


@pytest.mark.parametrize("role,budget", [("mentor", 1500), ("analysis", 2500)])
async def test_claude_structured_role_probe_validates_existing_contract(
    data, api, monkeypatch, role, budget
):
    await prepare(api)

    async def upstream(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == budget and "stream" not in payload
        schema = payload["output_config"]["format"]
        assert (
            schema["type"] == "json_schema"
            and schema["schema"]["additionalProperties"] is False
        )
        if role == "analysis" and len(calls) == 1:
            assert "maximum" not in schema["schema"]["properties"]["confidence"]
        value = json.dumps(
            (MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE)
            if role == "mentor"
            else CLASSIFICATION if len(calls) == 1 else SYNTHESIS
        )
        return httpx2.Response(200, json=response_body(value))

    clients, calls = sdk_transport(
        monkeypatch, upstream, "anthropic_generation"
    )
    body = dict(expected_version=1, role=role, request_id=str(uuid4()))
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert state["verification_state"][role]["status"] == "succeeded"
    assert all(
        v["status"] == "unverified"
        for r, v in state["verification_state"].items()
        if r != role
    )
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text("SELECT probe_step,status FROM api_usage_log ORDER BY id")
            )
        ).all()
        assert rows == [
            (
                "manual_positive" if role == "mentor" else "classification",
                "completed",
            ),
            ("auto_negative" if role == "mentor" else "synthesis", "completed"),
        ]


async def test_claude_failures_stop_bundle_preserve_usage_and_hide_bodies(
    data, api, monkeypatch, caplog
):
    await prepare(api)
    cases = [
        ("student", 1, "context_http", "context_limit"),
        ("student", 1, "refusal", "refused"),
        ("student", 1, "max_tokens", "output_limit"),
        ("student", 1, "model_context_window_exceeded", "context_limit"),
        ("student", 1, "future-stop", "invalid_output"),
        ("student", 1, "empty", "empty_response"),
        ("student", 2, "eof", "invalid_output"),
        ("student", 2, "stream_error", "transient"),
        ("student", 2, "refusal", "refused"),
        ("student", 2, "max_tokens", "output_limit"),
        ("student", 1, 401, "authentication"),
        ("student", 1, 403, "permission"),
        ("student", 1, 404, "model_unavailable"),
        ("student", 1, 429, "rate_limited"),
        ("student", 1, 529, "transient"),
        ("mentor", 1, "json", "invalid_json"),
        ("mentor", 1, "type", "invalid_output"),
        ("mentor", 1, "reason_empty", "empty_response"),
        ("analysis", 1, "label", "invalid_reference"),
        ("analysis", 1, "confidence", "invalid_output"),
        ("analysis", 2, "quote", "invalid_reference"),
    ]
    start_count = 0
    role, step, mode, expected = cases[0]

    async def upstream(request):
        payload = json.loads(request.content)
        current = len(calls) - start_count
        value = (
            MENTOR_POSITIVE
            if role == "mentor"
            else CLASSIFICATION if current == 1 else SYNTHESIS
        )
        response = response_body(
            json.dumps(value) if role != "student" else "合成"
        )
        if current == step:
            if mode == "context_http" or type(mode) is int:
                return httpx2.Response(
                    400 if mode == "context_http" else mode,
                    json={
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": (
                                "prompt is too long: PRIVATE-BODY"
                                if mode == "context_http"
                                else "PRIVATE-BODY"
                            ),
                        },
                    },
                )
            if mode == "json":
                response["content"] = [
                    {"type": "text", "text": '{"PRIVATE-BODY":'}
                ]
            elif mode in {
                "type",
                "reason_empty",
                "label",
                "confidence",
                "quote",
            }:
                value = json.loads(json.dumps(value))
                if mode == "type":
                    value["should_intervene"] = "false"
                if mode == "reason_empty":
                    value["reason_summary"] = " "
                if mode == "label":
                    value["label"] = "unknown"
                if mode == "confidence":
                    value["confidence"] = 1.1
                if mode == "quote":
                    value["strengths"][0]["quote"] = "PRIVATE-BODY"
                response["content"] = [
                    {"type": "text", "text": json.dumps(value)}
                ]
            elif mode == "empty":
                response["content"] = [{"type": "text", "text": " "}]
            elif step == 1:
                response["stop_reason"] = mode
        if payload.get("stream"):
            stream = stream_body(
                reason=(
                    mode if mode in {"refusal", "max_tokens"} else "end_turn"
                ),
                finish=mode not in {"eof", "stream_error"},
            )
            if mode == "stream_error":
                stream += sse(
                    "error",
                    error={
                        "type": "overloaded_error",
                        "message": "PRIVATE-BODY",
                    },
                )
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stream,
            )
        return httpx2.Response(200, json=response)

    clients, calls = sdk_transport(
        monkeypatch, upstream, "anthropic_generation"
    )
    for role, step, mode, expected in cases:
        start_count = len(calls)
        request_id = str(uuid4())
        assert (
            await write(
                api,
                "models/1/probes",
                expected_version=1,
                role=role,
                request_id=request_id,
            )
        ).status_code == 202
        result = await completed(api, request_id)
        assert (
            result["status"] == "failed" and result["error_code"] == expected
        ), mode
        assert len(calls) - start_count == step, mode
        assert all(c.is_closed() for c in clients)
        async with data.engine.connect() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status,error_code,attempt_no,input_tokens,output_tokens FROM api_usage_log WHERE request_id=:request_id ORDER BY id DESC LIMIT 1"
                    ),
                    {"request_id": request_id},
                )
            ).one()
            assert row[:3] == (
                "refused" if expected == "refused" else "failed",
                expected,
                1,
            )
            assert row[3:] == (
                (None, None)
                if type(mode) is int or mode == "context_http"
                else (15, 12 if step == 2 and role == "student" else 8)
            )
    state = (await api.get("/admin/ai/state")).json()
    assert "PRIVATE-" not in json.dumps(state) + caplog.text


async def test_native_option_preflight_rejects_unknowns_and_invalid_role_budget(
    data, api, monkeypatch
):
    await prepare(api)
    clients, calls = sdk_transport(
        monkeypatch,
        lambda request: pytest.fail("Preflight called SDK"),
        "anthropic_generation",
    )
    invalid = [
        {"top_p": 0.5},
        {"reasoning": {"effort": "low"}},
        {"max_output_tokens": True},
        {"max_output_tokens": 0},
        {"max_output_tokens": 128001},
        {"temperature": True},
        {"temperature": 1.01},
        {"temperature": "1"},
        {"thinking": {"type": "enabled", "budget_tokens": 1024}},
        {
            "thinking": {"type": "enabled", "budget_tokens": 1023},
            "max_output_tokens": 4096,
        },
        {"thinking": {"type": "adaptive", "budget_tokens": 1024}},
        {"thinking": {"type": "adaptive"}, "temperature": 0},
        {"thinking": {"type": "future"}},
        {"output_config": {"effort": "xhigh"}},
        {"output_config": {"format": {}}},
    ]
    for options in invalid:
        rejected = await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Claude",
            enabled=False,
            default_options=options,
        )
        assert rejected.status_code == 422, options
    for options in [
        {"thinking": {"type": "adaptive"}},
        {"thinking": {"type": "disabled"}, "temperature": 0},
        {"output_config": {"effort": "max"}},
        {
            "max_output_tokens": 4096,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        },
    ]:
        version = (await api.get("/admin/ai/state")).json()["models"][0][
            "config_version"
        ]
        assert (
            await write(
                api,
                "models/1/update",
                expected_version=version,
                display_name="Claude",
                enabled=False,
                default_options=options,
            )
        ).status_code == 200
    version = (await api.get("/admin/ai/state")).json()["models"][0][
        "config_version"
    ]
    rejected = await write(
        api,
        "models/1/probes",
        expected_version=version,
        role="student",
        request_id=str(uuid4()),
    )
    assert (
        rejected.status_code == 422
        and rejected.json()["detail"]["code"] == "invalid_options"
    )
    assert (
        await write(
            api,
            "models",
            provider="anthropic",
            model_id=MODEL + "-unknown",
            display_name="Unknown",
        )
    ).status_code == 200
    rejected = await write(
        api,
        "models/2/probes",
        expected_version=1,
        role="mentor",
        request_id=str(uuid4()),
    )
    assert (
        rejected.status_code == 422
        and rejected.json()["detail"]["code"]
        == "capability_definition_required"
    )
    assert not clients and not calls
    async with data.engine.connect() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM api_usage_log"))
        ).scalar_one() == 0

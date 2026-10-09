"""Gemini adapter contracts through the pinned SDK and mock HTTPX."""

import json

import httpx
import pytest
from test_google_catalog import install

from src.services import google_generation
from src.services.invocation_types import TextRequest

REQUEST = TextRequest(
    "google",
    "gemini-2.5-flash",
    "student",
    "System",
    [{"role": "user", "content": "Input"}],
    {"max_output_tokens": 1024, "thinking": {"budget": 0}, "temperature": 0},
    "request",
)
TIMEOUTS = dict(connect=5, student_total=30, student_first_output=1)


async def test_native_standard_price_requires_exact_model_and_thoughts(
    monkeypatch,
):
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/usage_pricing.json").read_text())[
        "google"
    ]
    payload = response(usage=fixture["usage"])
    payload["modelVersion"] = fixture["modelVersion"]

    async def upstream(request):
        return httpx.Response(200, json=payload)

    clients, calls = install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.type == "completed"
    assert event.usage["estimated_cost_usd"] == pytest.approx(fixture["cost"])
    assert event.usage["input_tokens"] == 100
    assert event.usage["output_tokens"] == 20
    assert event.usage["reasoning_tokens"] == 8
    assert event.usage["total_tokens"] == 120
    assert event.usage["pricing_as_of"] == "2026-10-09"
    assert (
        event.usage["pricing_source"]
        == "https://ai.google.dev/gemini-api/docs/pricing?hl=ja"
    )
    assert len(calls) == 1 and all(c.is_closed for c in clients)


@pytest.mark.parametrize(
    "change",
    [
        {"modelVersion": None},
        {"modelVersion": "gemini-2.5-flash-lite"},
        {"modelVersion": "gemini-2.5-flash-unknown"},
        {"usage": {"thoughtsTokenCount": None}},
        {"usage": {"cachedContentTokenCount": None}},
        {"usage": {"cachedContentTokenCount": 101}},
        {"usage": {"totalTokenCount": 128}},
    ],
)
async def test_missing_google_billing_dimensions_stay_unpriced(
    monkeypatch, change
):
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/usage_pricing.json").read_text())[
        "google"
    ]
    payload = response(usage=fixture["usage"])
    payload["modelVersion"] = fixture["modelVersion"]
    if "usage" in change:
        payload["usageMetadata"] = {**fixture["usage"], **change["usage"]}
    else:
        payload.update(change)

    async def upstream(request):
        return httpx.Response(200, json=payload)

    install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.type == "completed"
    assert event.usage["estimated_cost_usd"] is None
    assert (
        event.usage["pricing_as_of"] is None
        and event.usage["pricing_source"] is None
    )
    if change == {"usage": {"thoughtsTokenCount": None}}:
        assert event.usage["output_tokens"] is None


async def test_google_priced_stream_replaces_cumulative_counts_and_observed_zero(
    monkeypatch,
):
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/usage_pricing.json").read_text())[
        "google"
    ]
    for final_usage, expected in [
        (
            {
                "candidatesTokenCount": 12,
                "thoughtsTokenCount": 8,
                "totalTokenCount": 120,
            },
            0.0000692,
        ),
        ({key: 0 for key in fixture["usage"]}, 0.0),
    ]:
        first = response(
            finish=None,
            usage={
                **fixture["usage"],
                "candidatesTokenCount": 6,
                "thoughtsTokenCount": 4,
                "totalTokenCount": 110,
            },
        )
        first["modelVersion"] = fixture["modelVersion"]
        last = response(text="", usage=final_usage)

        async def upstream(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(first, last),
            )

        clients, calls = install(monkeypatch, upstream)
        events = [
            event
            async for event in google_generation.stream_text(
                REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
            )
        ]
        assert events[-1].type == "completed"
        assert events[-1].usage["estimated_cost_usd"] == pytest.approx(expected)
        assert (
            events[-1].usage["total_tokens"] == final_usage["totalTokenCount"]
        )
        assert len(calls) == 1 and all(c.is_closed for c in clients)


def response(text="Answer", finish="STOP", usage=None):
    return dict(
        candidates=[
            dict(
                content=dict(
                    role="model",
                    parts=[
                        {"thought": True, "text": "PRIVATE-THOUGHT"},
                        {"text": text},
                    ],
                ),
                finishReason=finish,
            )
        ],
        usageMetadata=usage,
    )


def sse(*values):
    return b"".join(f"data: {json.dumps(v)}\n\n".encode() for v in values)


async def test_text_options_and_usage_preserve_components(monkeypatch):
    async def upstream(request):
        assert (
            request.url.path
            == "/v1beta/models/gemini-2.5-flash:generateContent"
        )
        body = json.loads(request.content)
        assert body["generationConfig"]["thinkingConfig"] == {
            "thinking_budget": 0
        }
        assert body["generationConfig"]["temperature"] == 0
        assert body["contents"] == [
            {"role": "user", "parts": [{"text": "Input"}]}
        ]
        return httpx.Response(
            200,
            json=response(
                usage={
                    "promptTokenCount": 10,
                    "cachedContentTokenCount": 3,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 2,
                    "totalTokenCount": 16,
                }
            ),
        )

    clients, calls = install(monkeypatch, upstream)
    result = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert result.type == "completed" and result.text == "Answer", (
        result,
        [json.loads(r.content) for r in calls],
    )
    assert (
        result.usage["input_tokens"] == 10
        and result.usage["output_tokens"] == 6
    )
    assert (
        result.usage["reasoning_tokens"] == 2
        and result.usage["cache_read_tokens"] == 3
    )
    assert result.usage["total_tokens"] == 16 and result.usage["usage_complete"]
    assert result.usage["cache_write_tokens"] is None
    assert len(calls) == 1 and all(c.is_closed for c in clients)


@pytest.mark.parametrize(
    "mode,code",
    [
        ("success", None),
        ("limit", "output_limit"),
        ("refused", "refused"),
        ("prompt_block", "refused"),
        ("unknown", "invalid_output"),
        ("empty", "empty_response"),
        ("eof", "invalid_output"),
        ("error", "transient"),
    ],
)
async def test_stream_excludes_thoughts_and_requires_explicit_success(
    monkeypatch, mode, code
):
    first = response(
        text="" if mode == "empty" else "Visible",
        finish=None,
        usage={
            "promptTokenCount": 10,
            "candidatesTokenCount": 2,
            "thoughtsTokenCount": 1,
        },
    )
    last = response(
        text="",
        finish={
            "limit": "MAX_TOKENS",
            "refused": "SAFETY",
            "unknown": "FUTURE_FINISH",
        }.get(mode, "STOP"),
        usage={
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 2,
            "cachedContentTokenCount": 0,
            "totalTokenCount": 16,
        },
    )
    if mode == "prompt_block":
        first = {"promptFeedback": {"blockReason": "SAFETY"}}
    if mode == "error":
        last = {"error": {"code": 503, "message": "PRIVATE-ERROR"}}

    async def upstream(request):
        assert request.url.path.endswith(":streamGenerateContent")
        assert request.url.params["alt"] == "sse"
        content = (
            sse(first) if mode in ("eof", "prompt_block") else sse(first, last)
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=content
        )

    clients, calls = install(monkeypatch, upstream)
    events = [
        event
        async for event in google_generation.stream_text(
            REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
        )
    ]
    terminals = [e for e in events if e.type not in ("text_delta", "usage")]
    assert len(terminals) == 1 and terminals[0].error_code == code
    assert terminals[0].type == (
        "completed"
        if code is None
        else "refused" if code == "refused" else "error"
    )
    assert "PRIVATE" not in "".join(e.text for e in events)
    assert len(calls) == 1 and all(c.is_closed for c in clients)
    if mode == "success":
        assert "".join(e.text for e in events) == "Visible"
        assert (
            terminals[0].usage["output_tokens"] == 6
            and terminals[0].usage["input_tokens"] == 10
        )
        assert terminals[0].usage["total_tokens"] == 16


@pytest.mark.parametrize(
    "usage,output,complete",
    [
        (
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
                "totalTokenCount": 14,
            },
            None,
            False,
        ),
        (
            {
                "promptTokenCount": 0,
                "cachedContentTokenCount": 0,
                "candidatesTokenCount": 0,
                "thoughtsTokenCount": 0,
                "totalTokenCount": 0,
            },
            0,
            True,
        ),
        (None, None, False),
    ],
)
async def test_nonstream_usage_never_invents_missing_components(
    monkeypatch, usage, output, complete
):
    async def upstream(request):
        return httpx.Response(200, json=response(usage=usage))

    clients, _ = install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.type == "completed"
    assert (
        event.usage["output_tokens"] == output
        and event.usage["usage_complete"] is complete
    )
    assert event.usage["reasoning_tokens"] == (0 if complete else None)
    assert event.usage["cache_write_tokens"] is None and all(
        c.is_closed for c in clients
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("cancel", "interrupted"),
        ("first", "timeout_first_output"),
        ("total", "timeout_total"),
        ("read", "transient"),
    ],
)
async def test_stream_cancel_deadlines_and_read_error_close_response(
    monkeypatch, mode, code
):
    import asyncio

    entered = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            chunk = response(
                "Visible" if mode in ("total", "read") else "", finish=None
            )
            yield sse(chunk)
            entered.set()
            if mode == "read":
                raise httpx.ReadError("PRIVATE-ERROR")
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    body = Body()

    async def upstream(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=body
        )

    clients, calls = install(monkeypatch, upstream)
    limits = {
        **TIMEOUTS,
        "student_first_output": 0.03 if mode == "first" else 30,
        "student_total": 0.1 if mode == "total" else 30,
    }

    async def consume():
        return [
            event
            async for event in google_generation.stream_text(
                REQUEST, "PROVIDER-KEY-SENTINEL-1234", limits
            )
        ]

    task = asyncio.create_task(consume())
    if mode == "cancel":
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
    events = await asyncio.wait_for(task, 10)
    assert events[-1].error_code == code
    assert events[-1].type == ("interrupted" if mode == "cancel" else "error")
    assert body.closed and len(calls) == 1 and all(c.is_closed for c in clients)
    if mode == "first":
        assert not any(event.text.strip() for event in events)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (403, "permission"),
        (404, "model_unavailable"),
        (429, "rate_limited"),
        (503, "transient"),
        ("connect", "timeout_connect"),
    ],
)
async def test_google_failure_is_safe_and_never_retries(
    monkeypatch, caplog, status, code
):
    import logging

    caplog.set_level(logging.DEBUG)

    async def upstream(request):
        if status == "connect":
            raise httpx.ConnectTimeout("PRIVATE-ERROR", request=request)
        return httpx.Response(
            status, json={"error": {"code": status, "message": "PRIVATE-ERROR"}}
        )

    clients, calls = install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.type == "error" and event.error_code == code
    assert len(calls) == 1 and all(c.is_closed for c in clients)
    assert "PRIVATE-ERROR" not in str(event) + caplog.text


async def test_usage_only_trailer_keeps_latest_non_null_components(monkeypatch):
    async def upstream(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                response(
                    "Answer",
                    usage={
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 4,
                        "thoughtsTokenCount": 1,
                    },
                ),
                {
                    "usageMetadata": {
                        "promptTokenCount": None,
                        "thoughtsTokenCount": 2,
                        "cachedContentTokenCount": 0,
                        "totalTokenCount": 16,
                    }
                },
            ),
        )

    clients, _ = install(monkeypatch, upstream)
    events = [
        e
        async for e in google_generation.stream_text(
            REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
        )
    ]
    assert events[-1].type == "completed"
    assert (
        events[-1].usage["input_tokens"] == 10
        and events[-1].usage["output_tokens"] == 6
    )
    assert events[-1].usage["total_tokens"] == 16 and all(
        c.is_closed for c in clients
    )


async def test_depleted_credit_is_quota_and_retry_after_is_preserved(
    monkeypatch,
):
    status = 402

    async def upstream(request):
        return httpx.Response(
            status,
            headers={"Retry-After": "7"},
            json={"error": {"code": status, "message": "PRIVATE-ERROR"}},
        )

    clients, calls = install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.error_code == "quota"
    status = 429
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.error_code == "rate_limited" and event.retry_after_seconds == 7
    assert len(calls) == 2 and all(c.is_closed for c in clients)


async def test_unsupported_structured_schema_is_rejected_before_sdk(
    monkeypatch,
):
    from pydantic import BaseModel

    from src.services.invocation_types import StructuredRequest

    class Unsupported(BaseModel):
        data: dict[str, str]

    async def upstream(request):
        raise AssertionError("Unsupported schema must never call upstream")

    clients, calls = install(monkeypatch, upstream)
    request = StructuredRequest(**REQUEST.__dict__, output_schema=Unsupported)
    event = await google_generation.generate_structured(
        request, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert (
        event.type == "error"
        and event.error_code == "configuration_unavailable"
    )
    assert (
        not clients
        and not calls
        and event.structured is None
        and not event.text
    )


@pytest.mark.parametrize(
    "details,code",
    [
        (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "API_KEY_INVALID",
                }
            ],
            "authentication",
        ),
        (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "UNKNOWN_PRIVATE_REASON",
                }
            ],
            "invalid_output",
        ),
        ("PRIVATE-ERROR", "invalid_output"),
    ],
)
async def test_google_invalid_key_uses_structured_reason_without_leaking(
    monkeypatch, caplog, details, code
):
    async def upstream(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "PRIVATE-ERROR",
                    "details": details,
                }
            },
        )

    clients, calls = install(monkeypatch, upstream)
    event = await google_generation.generate_text(
        REQUEST, "PROVIDER-KEY-SENTINEL-1234", TIMEOUTS
    )
    assert event.type == "error" and event.error_code == code
    assert len(calls) == 1 and all(client.is_closed for client in clients)
    assert "PRIVATE" not in event.text + caplog.text

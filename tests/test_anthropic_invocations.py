"""Messages adapter contracts through the official SDK's HTTPX2 transport."""

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel
from test_anthropic_catalog import MODEL
from test_anthropic_probes import response_body, stream_body

from src.services import anthropic_generation
from src.services.invocation_types import StructuredRequest, TextRequest

REQUEST = TextRequest(
    "anthropic",
    MODEL,
    "student",
    "Synthetic system",
    [{"role": "user", "content": "Synthetic input"}],
    {"max_output_tokens": 1024},
    "synthetic-request",
)
TIMEOUTS = dict(connect=5, student_total=3, student_first_output=1)


def install(monkeypatch, handler):
    clients, calls = [], []

    def transport(request):
        calls.append(request)
        return handler(request)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        client = AsyncAnthropic(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(transport)
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(anthropic_generation, "AsyncAnthropic", factory)
    return clients, calls


async def test_spend_caps_are_permanent_quota_not_temporary_rate_limits(
    monkeypatch,
):
    for status, kind, detail, message, expected in [
        (
            429,
            "rate_limit_error",
            {"error_code": "enforced_spend_limit_reached"},
            "PRIVATE",
            "quota",
        ),
        (
            400,
            "invalid_request_error",
            {},
            "You have reached your specified API usage limits: PRIVATE",
            "quota",
        ),
        (
            400,
            "invalid_request_error",
            {},
            "You have reached your specified workspace API usage limits: PRIVATE",
            "quota",
        ),
        (402, "billing_error", {}, "PRIVATE", "quota"),
        (429, "rate_limit_error", {}, "PRIVATE", "rate_limited"),
    ]:
        clients, calls = install(
            monkeypatch,
            lambda request: httpx2.Response(
                status,
                json={
                    "type": "error",
                    "error": {
                        "type": kind,
                        "details": detail,
                        "message": message,
                    },
                },
            ),
        )
        event = await anthropic_generation.generate_text(
            REQUEST, "fake", TIMEOUTS
        )
        assert event.error_code == expected and event.text == ""
        assert len(calls) == 1 and all(c.is_closed() for c in clients)


class Node(BaseModel):
    value: str
    children: list["Node"]


class Recursive(BaseModel):
    root: Node


async def test_recursive_schema_is_rejected_before_sdk_construction(
    monkeypatch,
):
    clients, calls = install(
        monkeypatch, lambda request: httpx2.Response(200, json=response_body())
    )
    request = StructuredRequest(**REQUEST.__dict__, output_schema=Recursive)
    event = await anthropic_generation.generate_structured(
        request, "fake", TIMEOUTS
    )
    assert event.error_code == "configuration_unavailable"
    assert not clients and not calls


async def test_observed_native_billing_components_use_exact_model_rates(
    monkeypatch,
):
    payload = response_body()
    payload["usage"].update(
        service_tier="standard",
        inference_geo="global",
        cache_creation={
            "ephemeral_5m_input_tokens": 1,
            "ephemeral_1h_input_tokens": 1,
        },
        private="PRIVATE-USAGE",
    )
    clients, calls = install(
        monkeypatch, lambda request: httpx2.Response(200, json=payload)
    )
    event = await anthropic_generation.generate_text(REQUEST, "fake", TIMEOUTS)
    assert event.type == "completed"
    assert event.usage["estimated_cost_usd"] == pytest.approx(0.00016065)
    assert event.usage["pricing_as_of"] == "2026-10-09"
    assert (
        event.usage["pricing_source"]
        == "https://platform.claude.com/docs/en/about-claude/pricing"
    )
    assert (
        event.usage["total_tokens"] == 23
        and event.usage["reasoning_tokens"] == 5
    )
    assert (
        "PRIVATE" not in str(event.usage)
        and len(calls) == 1
        and all(c.is_closed() for c in clients)
    )


async def test_usage_unknowns_zeros_and_inconsistent_cache_breakdown_stay_unknown(
    monkeypatch,
):
    cases = [
        (
            {
                "input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 1,
                    "ephemeral_1h_input_tokens": 0,
                },
                "output_tokens": 0,
                "service_tier": "standard",
                "inference_geo": "global",
            },
            (0, 0, 0, None, None),
        ),
        (
            {
                "input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
                "service_tier": "standard",
                "inference_geo": "global",
                "output_tokens_details": {"thinking_tokens": 0},
            },
            (0, 0, 0, 0, 0),
        ),
        ({"input_tokens": 10, "output_tokens": 8}, (None, 8, None, None, None)),
        (
            {
                "input_tokens": 10,
                "output_tokens": 8,
                "cache_read_input_tokens": 3,
            },
            (None, 8, None, None, None),
        ),
        (
            {
                "input_tokens": 10,
                "output_tokens": 8,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
                "output_tokens_details": {"thinking_tokens": 9},
            },
            (15, 8, 23, None, None),
        ),
    ]
    for usage, expected in cases:
        clients, calls = install(
            monkeypatch,
            lambda request: httpx2.Response(
                200, json=response_body(usage=usage)
            ),
        )
        event = await anthropic_generation.generate_text(
            REQUEST, "fake", TIMEOUTS
        )
        assert event.type == "completed"
        assert (
            tuple(
                event.usage[key]
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "reasoning_tokens",
                    "estimated_cost_usd",
                )
            )
            == expected
        )
        assert event.usage["usage_complete"] is (
            expected[2] is not None and expected[3] is not None
        )
        assert len(calls) == 1 and all(c.is_closed() for c in clients)


@pytest.mark.parametrize("outcome", ["end_turn", "refusal", "max_tokens"])
@pytest.mark.parametrize("closer", ["stream", "client"])
async def test_cleanup_failure_preserves_observed_terminal(
    monkeypatch, outcome, closer
):
    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield stream_body(reason=outcome)

        async def aclose(self):
            if closer == "stream":
                raise httpx2.TransportError("PRIVATE-CLOSE-ERROR")

    class BrokenClose(httpx2.MockTransport):
        async def aclose(self):
            if closer == "client":
                raise httpx2.TransportError("PRIVATE-CLOSE-ERROR")

    def factory(**kwargs):
        return AsyncAnthropic(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=BrokenClose(
                    lambda request: httpx2.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=Body(),
                    )
                )
            ),
        )

    monkeypatch.setattr(anthropic_generation, "AsyncAnthropic", factory)
    events = [
        event
        async for event in anthropic_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    terminals = [
        event
        for event in events
        if event.type in {"completed", "refused", "error", "interrupted"}
    ]
    assert len(terminals) == 1
    assert (
        terminals[0].error_code
        == {
            "end_turn": None,
            "refusal": "refused",
            "max_tokens": "output_limit",
        }[outcome]
    )
    assert terminals[0].usage["output_tokens"] == 12 and "PRIVATE" not in str(
        events
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("success", None),
        ("eof", "invalid_output"),
        ("refusal", "refused"),
        ("max_tokens", "output_limit"),
        ("model_context_window_exceeded", "context_limit"),
        ("pause_turn", "invalid_output"),
        ("tool_use", "invalid_output"),
        ("empty", "empty_response"),
    ],
)
async def test_stream_only_exports_text_and_latest_cumulative_usage(
    monkeypatch, mode, expected
):
    payload = stream_body(
        content=" " if mode == "empty" else "visible",
        reason=(
            mode
            if mode
            in {
                "refusal",
                "max_tokens",
                "model_context_window_exceeded",
                "pause_turn",
                "tool_use",
            }
            else "end_turn"
        ),
        finish=mode != "eof",
    )
    clients, calls = install(
        monkeypatch,
        lambda request: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=payload
        ),
    )
    events = [
        event
        async for event in anthropic_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    terminals = [
        event
        for event in events
        if event.type in {"completed", "refused", "error", "interrupted"}
    ]
    assert len(terminals) == 1 and terminals[0].error_code == expected
    assert (
        terminals[0].usage["input_tokens"] == 15
        and terminals[0].usage["output_tokens"] == 12
    )
    assert [
        event.usage["output_tokens"]
        for event in events
        if event.type == "usage"
    ] == [8, 12]
    assert (
        "PRIVATE" not in str(events)
        and len(calls) == 1
        and all(c.is_closed() for c in clients)
    )
    assert terminals[0].text == ("visible" if expected is None else "")


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("first", "timeout_first_output"),
        ("total", "timeout_total"),
        ("cancel", "interrupted"),
        ("connect", "timeout_connect"),
    ],
)
async def test_timeouts_and_cancel_close_sdk_without_counting_thinking_as_first_text(
    monkeypatch, mode, expected
):
    import asyncio

    from test_student_probe import sse

    opened = asyncio.Event()
    closed = []
    message = response_body()
    message.update(content=[], stop_reason=None)
    start = sse("message_start", message=message)
    start += sse(
        "content_block_start",
        index=0,
        content_block={"type": "thinking", "thinking": "", "signature": ""},
    )
    start += sse(
        "content_block_delta",
        index=0,
        delta={"type": "thinking_delta", "thinking": "PRIVATE-THINKING"},
    )

    class Waiting(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield start
            opened.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    def transport(request):
        if mode == "connect":
            raise httpx2.ConnectTimeout("PRIVATE-TIMEOUT")
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Waiting()
        )

    clients, calls = install(monkeypatch, transport)
    policy = {
        **TIMEOUTS,
        "student_first_output": 0.03 if mode == "first" else 1,
        "student_total": 0.06 if mode == "total" else 3,
    }

    async def consume():
        return [
            event
            async for event in anthropic_generation.stream_text(
                REQUEST, "fake", policy
            )
        ]

    task = asyncio.create_task(consume())
    if mode == "cancel":
        async with asyncio.timeout(5):
            await opened.wait()
        task.cancel()
    events = await task
    assert events[-1].error_code == expected and not any(
        event.type == "text_delta" for event in events
    )
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    if mode != "connect":
        assert closed and events[-1].usage["input_tokens"] == 15
    assert "PRIVATE" not in str(events)


async def test_unverified_price_dimensions_never_use_a_fallback(monkeypatch):
    base = response_body()
    base["usage"].update(
        service_tier="standard",
        inference_geo="global",
        cache_creation={
            "ephemeral_5m_input_tokens": 1,
            "ephemeral_1h_input_tokens": 1,
        },
    )
    for field, value in [
        ("service_tier", None),
        ("service_tier", "priority"),
        ("service_tier", "batch"),
        ("inference_geo", None),
        ("inference_geo", "us"),
        ("cache_creation", None),
        (
            "cache_creation",
            {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 2},
        ),
    ]:
        payload = {**base, "usage": {**base["usage"], field: value}}
        clients, calls = install(
            monkeypatch, lambda request: httpx2.Response(200, json=payload)
        )
        event = await anthropic_generation.generate_text(
            REQUEST, "fake", TIMEOUTS
        )
        assert (
            event.type == "completed"
            and event.usage["estimated_cost_usd"] is None
        )
        assert (
            event.usage["pricing_as_of"] is None
            and event.usage["pricing_source"] is None
        )
        assert len(calls) == 1 and all(c.is_closed() for c in clients)
    clients, calls = install(
        monkeypatch,
        lambda request: httpx2.Response(
            200, json={**base, "model": "unverified-model"}
        ),
    )
    event = await anthropic_generation.generate_text(REQUEST, "fake", TIMEOUTS)
    assert event.usage["estimated_cost_usd"] is None and all(
        c.is_closed() for c in clients
    )

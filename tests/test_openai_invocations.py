"""Provider event/result contract using actual SDK parsing and HTTPX2."""

import httpx2
import pytest
from openai import AsyncOpenAI
from test_student_probe import response_body, sse

from src.services import openai_generation
from src.services.invocation_types import TextRequest

REQUEST = TextRequest(
    "openai",
    "gpt-5-mini",
    "student",
    "Synthetic system",
    [{"role": "user", "content": "Synthetic input"}],
    {"max_output_tokens": 1024},
    "synthetic-request",
)
TIMEOUTS = dict(connect=5, student_total=3, student_first_output=1)


def install(monkeypatch, payload, *, stream=False):
    clients = []

    def transport(request):
        if stream:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=payload,
            )
        return httpx2.Response(200, json=payload)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(transport)
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    return clients


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("refused", "refused"),
        ("output_limit", "output_limit"),
        ("context_limit", "context_limit"),
        ("empty", "empty_response"),
        ("unknown", "invalid_output"),
    ],
)
async def test_nonstream_never_promotes_refusal_incomplete_or_empty(
    monkeypatch, mode, expected
):
    payload = response_body()
    if mode == "refused":
        payload["output"][0]["content"] = [
            dict(type="refusal", refusal="PRIVATE-REFUSAL")
        ]
    elif mode == "output_limit":
        payload.update(
            status="incomplete",
            incomplete_details=dict(reason="max_output_tokens"),
        )
    elif mode == "context_limit":
        payload.update(
            status="failed",
            error=dict(code="context_length_exceeded", message="PRIVATE-ERROR"),
        )
    elif mode == "empty":
        payload["output"][0]["content"][0]["text"] = "  "
    else:
        payload["status"] = "unknown-future-status"
    clients = install(monkeypatch, payload)
    terminal = await openai_generation.generate_text(REQUEST, "fake", TIMEOUTS)
    assert terminal.type != "completed" and terminal.error_code == expected
    assert terminal.text == "" and all(c.is_closed() for c in clients)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("success", None),
        ("refused", "refused"),
        ("output_limit", "output_limit"),
        ("error", "transient"),
        ("eof", "invalid_output"),
        ("empty", "empty_response"),
        ("unknown", "invalid_output"),
    ],
)
async def test_stream_has_exactly_one_terminal_and_ignores_reasoning(
    monkeypatch, mode, expected
):
    payload = sse(
        "response.reasoning_text.delta",
        delta="PRIVATE-REASONING",
        sequence_number=0,
        item_id="r",
        output_index=0,
        content_index=0,
    )
    if mode != "empty":
        payload += sse(
            "response.output_text.delta",
            delta="visible",
            sequence_number=1,
            item_id="m",
            output_index=0,
            content_index=0,
        )
    final = response_body("visible" if mode != "empty" else "")
    if mode == "refused":
        final["output"][0]["content"] = [
            dict(type="refusal", refusal="PRIVATE-REFUSAL")
        ]
    if mode == "output_limit":
        final.update(
            status="incomplete",
            incomplete_details=dict(reason="max_output_tokens"),
        )
        payload += sse("response.incomplete", response=final, sequence_number=2)
    elif mode == "error":
        payload += sse(
            "error",
            code="server_error",
            message="PRIVATE-ERROR",
            param=None,
            sequence_number=2,
        )
    elif mode == "unknown":
        final["status"] = "future-status"
        payload += sse("response.completed", response=final, sequence_number=2)
    elif mode != "eof":
        payload += sse("response.completed", response=final, sequence_number=2)
    clients = install(monkeypatch, payload, stream=True)
    events = [
        event
        async for event in openai_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    terminal = [
        event
        for event in events
        if event.type in ("completed", "refused", "error", "interrupted")
    ]
    assert len(terminal) == 1 and terminal[0].error_code == expected
    assert terminal[0].type == (
        "completed"
        if expected is None
        else "refused" if expected == "refused" else "error"
    )
    assert all("PRIVATE" not in event.text for event in events)
    assert all(c.is_closed() for c in clients)


async def test_sdk_stream_error_uses_safe_classification(monkeypatch):
    payload = sse(
        "error", error=dict(code="server_error", message="PRIVATE-SDK-ERROR")
    )
    clients = install(monkeypatch, payload, stream=True)
    events = [
        event
        async for event in openai_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    assert len(events) == 1 and events[0].error_code == "transient"
    assert "PRIVATE" not in str(events) and all(c.is_closed() for c in clients)


async def test_stream_merges_latest_observed_usage_without_summing_or_inventing_zero(
    monkeypatch,
):
    first = dict(
        input_tokens=10,
        output_tokens=0,
        total_tokens=10,
        input_tokens_details=dict(cached_tokens=0),
        output_tokens_details=dict(reasoning_tokens=0),
        private="PRIVATE-USAGE",
    )
    later = dict(
        input_tokens=12,
        output_tokens=5,
        total_tokens=17,
        input_tokens_details=dict(cached_tokens=3),
    )
    payload = sse(
        "response.in_progress",
        response=response_body(usage=first),
        sequence_number=0,
    )
    payload += sse(
        "response.in_progress",
        response=response_body(usage=later),
        sequence_number=1,
    )
    payload += sse(
        "response.output_text.delta",
        delta="visible",
        sequence_number=2,
        item_id="m",
        output_index=0,
        content_index=0,
    )
    payload += sse(
        "response.completed",
        response=response_body("visible"),
        sequence_number=3,
    )
    clients = install(monkeypatch, payload, stream=True)
    events = [
        event
        async for event in openai_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    assert [
        event.usage["input_tokens"] for event in events if event.type == "usage"
    ] == [10, 12]
    usage = events[-1].usage
    assert (
        usage["input_tokens"] == 12
        and usage["output_tokens"] == 5
        and usage["total_tokens"] == 17
    )
    assert usage["cache_read_tokens"] == 3 and usage["reasoning_tokens"] == 0
    assert (
        usage["cache_write_tokens"] is None and usage["usage_complete"] is True
    )
    assert usage["raw_usage_json"]["input_tokens"] == 12
    assert "PRIVATE" not in str(usage["raw_usage_json"]) and all(
        c.is_closed() for c in clients
    )


@pytest.mark.parametrize(
    "usage,expected",
    [
        (
            dict(input_tokens=0, output_tokens=0, total_tokens=0),
            (0, 0, 0, None, None),
        ),
        (
            dict(
                input_tokens=10,
                output_tokens=5,
                total_tokens=99,
                input_tokens_details=dict(cached_tokens=20),
                output_tokens_details=dict(reasoning_tokens=8),
            ),
            (10, 5, None, None, None),
        ),
    ],
)
async def test_usage_preserves_observed_zero_and_rejects_inconsistent_totals(
    monkeypatch, usage, expected
):
    clients = install(monkeypatch, response_body(usage=usage))
    terminal = await openai_generation.generate_text(REQUEST, "fake", TIMEOUTS)
    assert terminal.type == "completed"
    assert (
        tuple(
            terminal.usage[key]
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_tokens",
                "reasoning_tokens",
            )
        )
        == expected
    )
    assert (
        terminal.usage["usage_complete"] is False
        and terminal.usage["cache_write_tokens"] is None
    )
    assert all(c.is_closed() for c in clients)


async def test_stream_cleanup_failure_cannot_emit_a_second_terminal(
    monkeypatch,
):
    payload = sse(
        "response.output_text.delta",
        delta="visible",
        sequence_number=0,
        item_id="m",
        output_index=0,
        content_index=0,
    )
    payload += sse(
        "response.completed",
        response=response_body("visible"),
        sequence_number=1,
    )

    class BrokenClose(httpx2.MockTransport):
        async def aclose(self):
            raise httpx2.TransportError("PRIVATE-CLOSE-ERROR")

    def factory(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=BrokenClose(
                    lambda request: httpx2.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        content=payload,
                    )
                )
            ),
        )

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    events = [
        event
        async for event in openai_generation.stream_text(
            REQUEST, "fake", TIMEOUTS
        )
    ]
    terminals = [
        event
        for event in events
        if event.type in ("completed", "refused", "interrupted", "error")
    ]
    assert len(terminals) == 1 and "PRIVATE" not in str(terminals)

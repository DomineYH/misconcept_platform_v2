"""Native model options through the actual pinned SDK HTTP transports."""

import json

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict
from test_anthropic_invocations import install as install_claude
from test_anthropic_probes import response_body as claude_response
from test_anthropic_probes import stream_body as claude_stream
from test_google_catalog import install as install_google
from test_google_invocations import response as google_response
from test_google_invocations import sse as google_stream
from test_model_native_options import GOOGLE, OPENAI
from test_student_probe import response_body as openai_response
from test_student_probe import sse

from src.services import (
    anthropic_generation,
    google_generation,
    openai_generation,
)
from src.services.invocation_types import StructuredRequest, TextRequest


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


NATIVE = (
    [
        ("openai", model, {"reasoning": {"effort": choices[-1]}})
        for model, choices, *_ in OPENAI
    ]
    + [
        ("anthropic", model, {"output_config": {"effort": "xhigh"}})
        for model in (
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
        )
    ]
    + [
        (
            "anthropic",
            "claude-sonnet-5-5",
            {
                "thinking": {"type": "between_tools"},
                "output_config": {"effort": "high"},
            },
        )
    ]
    + [
        ("google", model, {"thinking": {"level": choices[0]}})
        for model, choices, _ in GOOGLE
        if choices
    ]
    + [
        ("google", "gemini-2.5-flash", {"thinking": {"budget": budget}})
        for budget in (0, -1, 1024)
    ]
    + [
        (
            "openai",
            "gpt-6-sol",
            {"reasoning": {"effort": "none"}, "temperature": 0},
        )
    ]
    + [
        (provider, model, {})
        for provider, model in [
            ("openai", "gpt-6.1-sol"),
            ("anthropic", "claude-opus-5-5"),
            ("google", "gemini-3.8-flash"),
        ]
    ]
)


@pytest.mark.parametrize("provider,model,options", NATIVE)
@pytest.mark.parametrize("kind", ["text", "stream", "structured"])
async def test_native_options_reach_real_sdk_transport_unchanged(
    monkeypatch, provider, model, options, kind
):
    calls = []
    content = '{"answer":"ok"}' if kind == "structured" else "ok"
    if provider == "openai":
        adapter = openai_generation
        payload = openai_response(content=content)
        payload["model"] = model

        def upstream(request):
            calls.append(request)
            return (
                httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=sse(
                        "response.completed",
                        response=payload,
                        sequence_number=0,
                    ),
                )
                if kind == "stream"
                else httpx2.Response(200, json=payload)
            )

        def factory(**kwargs):
            return AsyncOpenAI(
                **kwargs,
                http_client=httpx2.AsyncClient(
                    transport=httpx2.MockTransport(upstream)
                ),
            )

        monkeypatch.setattr(adapter, "AsyncOpenAI", factory)
    elif provider == "anthropic":
        adapter = anthropic_generation
        payload = claude_response(content, model=model)
        _, calls = install_claude(
            monkeypatch,
            lambda request: (
                httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=claude_stream(),
                )
                if kind == "stream"
                else httpx2.Response(200, json=payload)
            ),
        )
    else:
        adapter = google_generation
        payload = google_response(text=content)
        payload["modelVersion"] = model

        async def upstream(request):
            return (
                httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=google_stream(payload),
                )
                if kind == "stream"
                else httpx.Response(200, json=payload)
            )

        _, calls = install_google(monkeypatch, upstream)
    request = TextRequest(
        provider,
        model,
        "student",
        "System",
        [{"role": "user", "content": "Input"}],
        {"max_output_tokens": 1024, **options},
        "native-options",
    )
    timeouts = dict(connect=5, student_total=30, student_first_output=5)
    if kind == "stream":
        events = [
            event
            async for event in adapter.stream_text(
                request, "PROVIDER-KEY-SENTINEL-1234", timeouts
            )
        ]
        result = events[-1]
    elif kind == "structured":
        result = await adapter.generate_structured(
            StructuredRequest(**request.__dict__, output_schema=Result),
            "PROVIDER-KEY-SENTINEL-1234",
            timeouts,
        )
    else:
        result = await adapter.generate_text(
            request, "PROVIDER-KEY-SENTINEL-1234", timeouts
        )
    assert result.type == "completed"
    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    if provider == "google":
        config = payload["generationConfig"]
        assert config["maxOutputTokens"] == 1024
        thinking = options.get("thinking")
        assert config.get("thinkingConfig") == (
            None
            if thinking is None
            else {
                (
                    "thinking_level"
                    if "level" in thinking
                    else "thinking_budget"
                ): (
                    {"low": "LOW", "minimal": "MINIMAL"}[thinking["level"]]
                    if "level" in thinking
                    else thinking["budget"]
                )
            }
        )
    else:
        assert payload["model"] == model
        assert (
            payload[
                "max_tokens" if provider == "anthropic" else "max_output_tokens"
            ]
            == 1024
        )
        for key in ("reasoning", "thinking", "temperature"):
            assert payload.get(key) == options.get(key)
        if provider == "anthropic":
            assert payload.get("output_config", {}).get(
                "effort"
            ) == options.get("output_config", {}).get("effort")
    if model not in (
        "gpt-5.2",
        "gpt-5-mini",
        "gpt-5.2-2025-12-11",
        "gpt-5-mini-2025-08-07",
        "gemini-2.5-flash",
    ):
        assert result.usage["estimated_cost_usd"] is None

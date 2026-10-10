"""S3 mentor semantics at each provider's real SDK mock transport boundary."""

import json

import httpx
import httpx2
import pytest
from test_provider_connections import KEY
from test_role_probes import MENTOR_POSITIVE

from src.services import (
    anthropic_generation,
    google_generation,
    openai_generation,
)
from src.services.invocation_types import StructuredRequest
from src.services.role_output_contracts import MentorOutput


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
@pytest.mark.parametrize(
    "changes,trigger,code",
    [
        ({}, "manual", None),
        ({}, "auto", None),
        ({"should_intervene": False, "feedback": ""}, "auto", None),
        (
            {"should_intervene": False, "feedback": ""},
            "manual",
            "invalid_output",
        ),
        ({"should_intervene": "true"}, "auto", "invalid_output"),
        ({"should_intervene": 1}, "auto", "invalid_output"),
        ({"feedback": 1}, "auto", "invalid_output"),
        ({"reason_summary": 1}, "auto", "invalid_output"),
        ({"unknown": "PRIVATE-EXTRA"}, "auto", "invalid_output"),
        ({"feedback": "x" * 50_001}, "auto", "invalid_output"),
        ({"reason_summary": "x" * 2_001}, "auto", "invalid_output"),
        ({"feedback": ""}, "auto", "empty_response"),
        ({"feedback": " \n\t"}, "auto", "empty_response"),
        ({"reason_summary": ""}, "auto", "empty_response"),
        ({"reason_summary": " \n\t"}, "auto", "empty_response"),
        ({"should_intervene": False}, "auto", "invalid_output"),
        (
            {"should_intervene": False, "feedback": " "},
            "auto",
            "invalid_output",
        ),
        (
            {"feedback": "x" * 50_000, "reason_summary": "x" * 2_000},
            "auto",
            None,
        ),
        ("missing_should_intervene", "auto", "invalid_output"),
        ("missing_feedback", "auto", "invalid_output"),
        ("missing_reason_summary", "auto", "invalid_output"),
        ("invalid_json", "auto", "invalid_json"),
    ],
)
async def test_mentor_strict_output_contract(
    monkeypatch, provider, changes, trigger, code
):
    value = (
        {**MENTOR_POSITIVE, **changes}
        if isinstance(changes, dict)
        else dict(MENTOR_POSITIVE)
    )
    if isinstance(changes, str) and changes.startswith("missing_"):
        del value[changes.removeprefix("missing_")]
    content = (
        '{"PRIVATE-PARTIAL":'
        if changes == "invalid_json"
        else json.dumps(value)
    )
    if provider == "openai":
        from test_student_probe import response_body, sdk_transport

        async def upstream(request, body):
            return httpx2.Response(
                200,
                json=response_body(
                    content,
                    usage={
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                ),
            )

        clients, calls = sdk_transport(monkeypatch, upstream, budget=1500)
        model, adapter = "gpt-5-mini", openai_generation
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport
        from test_anthropic_probes import response_body

        async def upstream(request):
            return httpx2.Response(200, json=response_body(content))

        clients, calls = sdk_transport(
            monkeypatch, upstream, "anthropic_generation"
        )
        model, adapter = "claude-sonnet-4-6", anthropic_generation
    else:
        from test_google_catalog import install
        from test_google_invocations import response

        async def upstream(request):
            return httpx.Response(200, json=response(content))

        clients, calls = install(monkeypatch, upstream)
        model, adapter = "gemini-2.5-flash", google_generation
    request = StructuredRequest(
        provider,
        model,
        "mentor",
        "Synthetic mentor contract",
        [{"role": "user", "content": "Synthetic dialogue"}],
        {"max_output_tokens": 1500},
        "mentor-contract",
        MentorOutput,
        {"trigger": trigger},
    )
    event = await adapter.generate_structured(
        request, KEY, {"connect": 5, "mentor_total": 3}
    )
    assert event.type == ("error" if code else "completed")
    assert event.error_code == code
    assert event.text == "" and event.structured == (None if code else value)
    assert event.response_received and event.usage is not None
    if provider == "openai":
        assert event.usage["total_tokens"] == 0
    assert len(calls) == 1
    assert all(
        c.is_closed if provider == "google" else c.is_closed() for c in clients
    )

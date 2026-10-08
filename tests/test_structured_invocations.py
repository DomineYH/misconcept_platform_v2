"""Structured adapter contract through the real SDK mock transport."""

import json

import pytest
from pydantic import BaseModel, ConfigDict, RootModel
from test_openai_invocations import install
from test_student_probe import response_body

from src.services import openai_generation
from src.services.invocation_types import (
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.role_probe_contract import probe_steps


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    is_repetitive: bool
    is_inappropriate: bool
    reason: str


def test_unknown_probe_role_fails_closed():
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        probe_steps("openai", "gpt-5-mini", "greeting", {}, "request")


def request(schema=Judgment):
    return StructuredRequest(
        "openai",
        "gpt-5-mini",
        "mentor",
        "Synthetic contract",
        [{"role": "user", "content": "Synthetic dialogue"}],
        {"max_output_tokens": 1500},
        "structured-contract",
        schema,
    )


async def test_structured_result_is_validated_and_never_exposes_raw_json(
    monkeypatch,
):
    value = {"is_repetitive": False, "is_inappropriate": True, "reason": "반복"}
    clients = install(monkeypatch, response_body(json.dumps(value)))
    event = await openai_generation.generate_structured(
        request(), "synthetic-key", {"connect": 5, "mentor_total": 3}
    )
    assert event.type == "completed" and event.structured == value
    assert event.text == "" and all(c.is_closed() for c in clients)


class OpenDictionary(BaseModel):
    values: dict[str, str]


class TupleOutput(BaseModel):
    values: tuple[int, int]


class ExtraOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: str


@pytest.mark.parametrize(
    "schema", [OpenDictionary, TupleOutput, ExtraOutput, RootModel[list[str]]]
)
async def test_unsupported_schema_is_rejected_before_sdk_call(
    monkeypatch, schema
):

    clients = install(monkeypatch, response_body("{}"))
    result = await openai_generation.generate_structured(
        request(schema), "synthetic-key", {"connect": 5, "mentor_total": 3}
    )
    assert result.error_code == "configuration_unavailable"
    assert clients == []


async def test_structured_call_requires_schema_before_any_sdk_client(
    monkeypatch,
):
    clients = install(monkeypatch, response_body("{}"))
    plain = TextRequest(
        "openai", "gpt-5-mini", "mentor", "Synthetic", [], {}, "request"
    )
    event = await openai_generation.generate_structured(
        plain, "synthetic-key", {"connect": 5, "mentor_total": 3}
    )
    assert event.error_code == "configuration_unavailable" and clients == []


@pytest.mark.parametrize(
    "content,code",
    [
        ('{"private":', "invalid_json"),
        (
            '{"is_repetitive":NaN,"is_inappropriate":true,"reason":"private"}',
            "invalid_json",
        ),
        (
            '{"is_repetitive":"false","is_inappropriate":true,"reason":"private"}',
            "invalid_output",
        ),
    ],
)
async def test_invalid_structured_result_retains_usage_without_partial_json(
    monkeypatch, content, code
):
    clients = install(
        monkeypatch,
        response_body(
            content,
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        ),
    )
    event = await openai_generation.generate_structured(
        request(), "synthetic-key", {"connect": 5, "mentor_total": 3}
    )
    assert event.type == "error" and event.error_code == code
    assert (
        event.text == ""
        and event.structured is None
        and event.usage["total_tokens"] == 15
    )
    assert all(c.is_closed() for c in clients)

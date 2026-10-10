"""Provider-neutral text request and event contract for new invocations."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class TextRequest:
    provider: str
    model_id: str
    role: str
    system_instruction: str
    messages: list[dict]
    validated_options: dict
    request_id: str
    context_budget_json: dict | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class StructuredRequest(TextRequest):
    output_schema: type[BaseModel]
    validation_context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CallEvent:
    type: Literal[
        "text_delta", "usage", "completed", "refused", "interrupted", "error"
    ]
    text: str = ""
    usage: dict | None = None
    error_code: str | None = None
    retry_after_seconds: float | None = None
    response_received: bool = False
    models: list[dict] | None = None
    structured: dict | None = None


class InvocationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def empty_usage():
    return dict(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cache_read_tokens=None,
        reasoning_tokens=None,
        cache_write_tokens=None,
        usage_complete=False,
        raw_usage_json=None,
        estimated_cost_usd=None,
        pricing_as_of=None,
        pricing_source=None,
    )

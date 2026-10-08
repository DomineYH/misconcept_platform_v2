"""Provider-neutral text request and event contract for new invocations."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TextRequest:
    provider: str
    model_id: str
    role: str
    system_instruction: str
    messages: list[dict]
    validated_options: dict
    request_id: str


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


class InvocationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

"""Request-scoped Responses text/stream adapter; no lesson-path changes."""

import asyncio

import httpx2
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from src.services.invocation_types import CallEvent, InvocationError
from src.services.model_capabilities import validate_model_and_options
from src.services.openai_catalog import status_code
from src.services.openai_usage import normalize_usage


def parameters(request):
    if request.provider != "openai" or any(
        m.get("role") not in ("user", "assistant")
        or not isinstance(m.get("content"), str)
        for m in request.messages
    ):
        raise InvocationError("invalid_output")
    try:
        options = validate_model_and_options(
            request.provider, request.model_id, request.validated_options
        )
    except ValueError:
        raise InvocationError("configuration_unavailable") from None
    return dict(
        model=request.model_id,
        instructions=request.system_instruction,
        input=request.messages,
        store=False,
        **options,
    )


def response_error(code):
    if not isinstance(code, str):
        return "invalid_output"
    return {
        "context_length_exceeded": "context_limit",
        "context_window_exceeded": "context_limit",
        "server_error": "transient",
        "rate_limit_exceeded": "rate_limited",
        "insufficient_quota": "quota",
        "invalid_api_key": "authentication",
        "model_not_found": "model_unavailable",
    }.get(code, "invalid_output")


def exception_code(error):
    if isinstance(error, InvocationError):
        return error.code
    if isinstance(error, APITimeoutError):
        return (
            "timeout_connect"
            if isinstance(error.__cause__, httpx2.ConnectTimeout)
            else "timeout_total"
        )
    if isinstance(error, APIError):
        body = error.body if isinstance(error.body, dict) else {}
        code = response_error(body.get("code"))
        if code != "invalid_output":
            return code
        if isinstance(error, APIStatusError):
            return status_code(error)
    if isinstance(error, (APIConnectionError, httpx2.TransportError)):
        return "transient"
    return "invalid_output"


def result(response, previous_usage=None):
    usage = normalize_usage(getattr(response, "usage", None), previous_usage)
    status = getattr(response, "status", None)
    if status == "incomplete":
        reason = getattr(
            getattr(response, "incomplete_details", None), "reason", None
        )
        code = {
            "max_output_tokens": "output_limit",
            "content_filter": "refused",
        }.get(reason, "invalid_output")
        return CallEvent(
            "refused" if code == "refused" else "error",
            usage=usage,
            error_code=code,
        )
    if status == "failed":
        return CallEvent(
            "error",
            usage=usage,
            error_code=response_error(
                getattr(getattr(response, "error", None), "code", None)
            ),
        )
    if status != "completed":
        return CallEvent("error", usage=usage, error_code="invalid_output")
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return CallEvent("error", usage=usage, error_code="invalid_output")
    for item in output:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", None) == "refusal":
                return CallEvent("refused", usage=usage, error_code="refused")
    text = response.output_text
    if not isinstance(text, str) or not text.strip():
        return CallEvent("error", usage=usage, error_code="empty_response")
    return CallEvent("completed", text=text, usage=usage)


async def generate_text(request, secret, timeouts):
    try:
        async with asyncio.timeout(timeouts["student_total"]):
            async with AsyncOpenAI(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(
                    timeouts["student_total"], connect=timeouts["connect"]
                ),
            ) as client:
                return result(
                    await client.responses.create(**parameters(request))
                )
    except asyncio.CancelledError:
        return CallEvent("interrupted", error_code="interrupted")
    except TimeoutError:
        return CallEvent("error", error_code="timeout_total")
    except (
        APIError,
        httpx2.TransportError,
        ValueError,
        TypeError,
        AttributeError,
        InvocationError,
    ) as error:
        return CallEvent("error", error_code=exception_code(error))


async def stream_text(request, secret, timeouts):
    first = None
    displayed = False
    refused = False
    usage = None
    terminal = None
    try:
        async with asyncio.timeout(timeouts["student_total"]):
            async with AsyncOpenAI(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(
                    timeouts["student_total"], connect=timeouts["connect"]
                ),
            ) as client:
                async with asyncio.timeout(
                    timeouts["student_first_output"]
                ) as first:
                    stream = await client.responses.create(
                        **parameters(request), stream=True
                    )
                    async with stream:
                        async for event in stream:
                            response = getattr(event, "response", None)
                            if (
                                response is not None
                                and getattr(response, "usage", None) is not None
                            ):
                                usage = normalize_usage(response.usage, usage)
                                yield CallEvent("usage", usage=usage)
                            if event.type == "response.output_text.delta":
                                if not isinstance(event.delta, str):
                                    raise InvocationError("invalid_output")
                                if event.delta.strip():
                                    displayed = True
                                    first.reschedule(None)
                                yield CallEvent("text_delta", text=event.delta)
                            elif event.type in (
                                "response.refusal.delta",
                                "response.refusal.done",
                            ):
                                refused = True
                            elif event.type in (
                                "response.completed",
                                "response.failed",
                                "response.incomplete",
                            ):
                                terminal = result(event.response, usage)
                                if refused:
                                    terminal = CallEvent(
                                        "refused",
                                        usage=terminal.usage,
                                        error_code="refused",
                                    )
                                elif (
                                    terminal.type == "completed"
                                    and not displayed
                                ):
                                    terminal = CallEvent(
                                        "error",
                                        usage=terminal.usage,
                                        error_code="empty_response",
                                    )
                                usage = terminal.usage
                                break
                            elif event.type == "error":
                                terminal = CallEvent(
                                    "error",
                                    usage=usage,
                                    error_code=response_error(event.code),
                                )
                                break
    except asyncio.CancelledError:
        terminal = terminal or CallEvent(
            "interrupted", usage=usage, error_code="interrupted"
        )
    except TimeoutError:
        terminal = terminal or CallEvent(
            "error",
            usage=usage,
            error_code=(
                "timeout_first_output"
                if first is not None and first.expired()
                else "timeout_total"
            ),
        )
    except Exception as error:
        terminal = terminal or CallEvent(
            "error", usage=usage, error_code=exception_code(error)
        )
    yield terminal or CallEvent(
        "error", usage=usage, error_code="invalid_output"
    )

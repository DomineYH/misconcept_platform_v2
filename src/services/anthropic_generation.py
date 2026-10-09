"""Request-scoped Claude Messages text/stream adapter using the common policy."""

import asyncio
import logging
from contextlib import nullcontext
from dataclasses import replace

import httpx2
from anthropic import APIStatusError, AsyncAnthropic, transform_schema

from src.services.anthropic_errors import exception_code
from src.services.anthropic_usage import normalize_usage
from src.services.call_policy import CallDeadline, retry_after
from src.services.invocation_types import (
    CallEvent,
    InvocationError,
    StructuredRequest,
)
from src.services.model_capabilities import validate_model_and_options
from src.services.structured_output import strict_schema, structured_event

logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


def output_schema(model):
    schema = strict_schema(model)

    def check(node, references):
        if isinstance(node, list):
            for child in node:
                check(child, references)
        elif isinstance(node, dict):
            if any(
                isinstance(value, (dict, list))
                for value in node.get("enum", [])
            ):
                raise InvocationError("configuration_unavailable")
            if "$ref" in node:
                ref = node["$ref"]
                target = schema.get("$defs", {}).get(
                    ref.removeprefix("#/$defs/")
                )
                if (
                    not ref.startswith("#/$defs/")
                    or target is None
                    or ref in references
                ):
                    raise InvocationError("configuration_unavailable")
                check(target, references | {ref})
            for name, child in node.items():
                if name != "$defs":
                    check(child, references)

    check(schema, set())
    return transform_schema(schema)


def parameters(request):
    if request.provider != "anthropic" or any(
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
    maximum = options.pop("max_output_tokens", 1024)
    values = dict(
        model=request.model_id,
        system=request.system_instruction,
        messages=request.messages,
        max_tokens=maximum,
        **options,
    )
    if isinstance(request, StructuredRequest):
        values.setdefault("output_config", {})["format"] = {
            "type": "json_schema",
            "schema": output_schema(request.output_schema),
        }
    return values


async def generate_structured(request, secret, timeouts, *, deadline=None):
    try:
        if not isinstance(request, StructuredRequest):
            raise InvocationError("configuration_unavailable")
        parameters(request)
    except Exception as error:
        return error_event(error)
    event = await generate_text(request, secret, timeouts, deadline=deadline)
    return structured_event(request, event)


def terminal(reason, text, usage):
    code = {
        "refusal": "refused",
        "max_tokens": "output_limit",
        "model_context_window_exceeded": "context_limit",
    }.get(reason)
    if code:
        return CallEvent(
            "refused" if code == "refused" else "error",
            usage=usage,
            error_code=code,
        )
    if reason not in ("end_turn", "stop_sequence"):
        return CallEvent("error", usage=usage, error_code="invalid_output")
    if not text.strip():
        return CallEvent("error", usage=usage, error_code="empty_response")
    return CallEvent("completed", text=text, usage=usage)


def error_event(error, usage=None, received=False):
    return CallEvent(
        "error",
        usage=usage,
        error_code=exception_code(error),
        retry_after_seconds=(
            retry_after(error.response.headers.get("Retry-After"))
            if isinstance(error, APIStatusError)
            else None
        ),
        response_received=received
        or (isinstance(error, APIStatusError) and error.status_code == 200),
    )


async def generate_text(request, secret, timeouts, *, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(timeouts, request.role)
    usage = None
    try:
        values = parameters(request)
        async with deadline.total() if owned else nullcontext():
            async with AsyncAnthropic(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(
                    timeouts[f"{request.role}_total"],
                    connect=timeouts["connect"],
                ),
            ) as client:
                response = await client.messages.create(**values)
                usage = normalize_usage(
                    getattr(response, "usage", None), model_id=response.model
                )
                if (
                    response.type != "message"
                    or response.role != "assistant"
                    or not isinstance(response.content, list)
                ):
                    raise InvocationError("invalid_output")
                text = ""
                for block in response.content:
                    if block.type == "text":
                        if not isinstance(block.text, str):
                            raise InvocationError("invalid_output")
                        text += block.text
                    elif block.type not in ("thinking", "redacted_thinking"):
                        raise InvocationError("invalid_output")
                return replace(
                    terminal(response.stop_reason, text, usage),
                    response_received=True,
                )
    except asyncio.CancelledError:
        return CallEvent(
            "error" if deadline.expired() else "interrupted",
            usage=usage,
            error_code="timeout_total" if deadline.expired() else "interrupted",
        )
    except Exception as error:
        return error_event(error, usage, received=usage is not None)


async def stream_text(request, secret, timeouts, *, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(timeouts, request.role)
    first = None
    usage = outcome = reason = None
    text = ""
    blocks = {}
    started = received = False
    model_id = None
    try:
        values = parameters(request)
        async with deadline.total() if owned else nullcontext():
            async with AsyncAnthropic(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(
                    timeouts[f"{request.role}_total"],
                    connect=timeouts["connect"],
                ),
            ) as client:
                async with deadline.first() as first:
                    stream = await client.messages.create(**values, stream=True)
                    received = True
                    async with stream:
                        async for event in stream:
                            if event.type == "message_start":
                                if (
                                    started
                                    or event.message.role != "assistant"
                                    or event.message.type != "message"
                                ):
                                    raise InvocationError("invalid_output")
                                started = True
                                model_id = event.message.model
                                usage = normalize_usage(
                                    event.message.usage,
                                    usage,
                                    model_id=model_id,
                                )
                                yield CallEvent("usage", usage=usage)
                            elif event.type == "content_block_start":
                                block = event.content_block
                                if (
                                    not started
                                    or event.index in blocks
                                    or block.type
                                    not in (
                                        "text",
                                        "thinking",
                                        "redacted_thinking",
                                    )
                                ):
                                    raise InvocationError("invalid_output")
                                blocks[event.index] = block.type
                                if block.type == "text":
                                    if not isinstance(block.text, str):
                                        raise InvocationError("invalid_output")
                                    text += block.text
                                    if block.text.strip():
                                        first.reschedule(None)
                                    yield CallEvent(
                                        "text_delta", text=block.text
                                    )
                            elif event.type == "content_block_delta":
                                delta = event.delta
                                if delta.type == "text_delta":
                                    if blocks.get(
                                        event.index
                                    ) != "text" or not isinstance(
                                        delta.text, str
                                    ):
                                        raise InvocationError("invalid_output")
                                    text += delta.text
                                    if delta.text.strip():
                                        first.reschedule(None)
                                    yield CallEvent(
                                        "text_delta", text=delta.text
                                    )
                                elif (
                                    delta.type
                                    not in ("thinking_delta", "signature_delta")
                                    or blocks.get(event.index) != "thinking"
                                ):
                                    raise InvocationError("invalid_output")
                            elif event.type == "content_block_stop":
                                if event.index not in blocks:
                                    raise InvocationError("invalid_output")
                                del blocks[event.index]
                            elif event.type == "message_delta":
                                if not started:
                                    raise InvocationError("invalid_output")
                                reason = event.delta.stop_reason
                                usage = normalize_usage(
                                    event.usage, usage, model_id=model_id
                                )
                                yield CallEvent("usage", usage=usage)
                            elif event.type == "message_stop":
                                if not started or blocks:
                                    raise InvocationError("invalid_output")
                                outcome = replace(
                                    terminal(reason, text, usage),
                                    response_received=True,
                                )
                                break
    except asyncio.CancelledError:
        outcome = outcome or CallEvent(
            "error" if deadline.expired() else "interrupted",
            usage=usage,
            error_code="timeout_total" if deadline.expired() else "interrupted",
            response_received=received,
        )
    except TimeoutError:
        outcome = outcome or CallEvent(
            "error",
            usage=usage,
            error_code=(
                "timeout_first_output"
                if first is not None and first.expired()
                else "timeout_total"
            ),
            response_received=received,
        )
    except Exception as error:
        outcome = outcome or error_event(error, usage, received)
    yield outcome or CallEvent(
        "error",
        usage=usage,
        error_code="invalid_output",
        response_received=received,
    )

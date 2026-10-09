"""Gemini generateContent adapter using the common request/result contract."""

import asyncio
from contextlib import aclosing, nullcontext
from dataclasses import replace

from src.services.call_policy import CallDeadline
from src.services.google_client import exception_event, scoped_client
from src.services.google_usage import normalize_usage
from src.services.invocation_types import (
    CallEvent,
    InvocationError,
    StructuredRequest,
)
from src.services.model_capabilities import validate_model_and_options
from src.services.structured_output import strict_schema, validate_output


def parameters(request):
    if request.provider != "google" or any(
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
    config = {"system_instruction": request.system_instruction}
    for name in ("max_output_tokens", "temperature"):
        if name in options:
            config[name] = options[name]
    if "thinking" in options:
        config["thinking_config"] = {
            f"thinking_{key}": value
            for key, value in options["thinking"].items()
        }
    if isinstance(request, StructuredRequest):
        config["response_mime_type"] = "application/json"
        config["response_json_schema"] = google_schema(request.output_schema)
    return dict(
        model=request.model_id,
        contents=[
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in request.messages
        ],
        config=config,
    )


def result(response, previous_usage=None, *, streaming=False, displayed=False):
    usage = normalize_usage(
        response.usage_metadata, previous_usage, model_id=response.model_version
    )
    feedback = response.prompt_feedback
    block = getattr(feedback, "block_reason", None)
    code = None
    if block is not None:
        code = (
            "refused"
            if block
            in (
                "SAFETY",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "IMAGE_SAFETY",
                "OTHER",
            )
            else "invalid_output"
        )
    candidates = response.candidates
    if code is None and (not candidates or len(candidates) != 1):
        code = "invalid_output"
    candidate = candidates[0] if candidates else None
    finish = candidate.finish_reason if candidate else None
    if code is None and finish != "STOP" and not (streaming and finish is None):
        code = {
            "MAX_TOKENS": "output_limit",
            "SAFETY": "refused",
            "RECITATION": "refused",
            "BLOCKLIST": "refused",
            "PROHIBITED_CONTENT": "refused",
            "SPII": "refused",
            "IMAGE_SAFETY": "refused",
        }.get(finish, "invalid_output")
    text = ""
    if code is None and candidate.content:
        for part in candidate.content.parts or []:
            if part.thought:
                continue
            if part.text is None:
                code = "invalid_output"
                break
            text += part.text
    if code is None and finish == "STOP" and not (text.strip() or displayed):
        code = "empty_response"
    if code:
        return CallEvent(
            "refused" if code == "refused" else "error",
            usage=usage,
            error_code=code,
            response_received=True,
        )
    return CallEvent(
        "text_delta" if finish is None else "completed",
        text=text,
        usage=usage,
        response_received=True,
    )


async def generate_text(request, secret, timeouts, *, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(timeouts, request.role)
    try:
        values = parameters(request)
        async with deadline.total() if owned else nullcontext():
            async with scoped_client(secret, timeouts["connect"]) as client:
                return result(await client.models.generate_content(**values))
    except asyncio.CancelledError:
        return CallEvent(
            "error" if deadline.expired() else "interrupted",
            error_code="timeout_total" if deadline.expired() else "interrupted",
        )
    except TimeoutError:
        return CallEvent("error", error_code="timeout_total")
    except Exception as error:
        return exception_event(error)


async def stream_text(request, secret, timeouts, *, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(timeouts, request.role)
    first = terminal = usage = None
    displayed = received = False
    try:
        values = parameters(request)
        async with deadline.total() if owned else nullcontext():
            async with scoped_client(secret, timeouts["connect"]) as client:
                async with deadline.first() as first:
                    stream = await client.models.generate_content_stream(
                        **values
                    )
                    async with aclosing(stream):
                        async for response in stream:
                            received = True
                            if response.usage_metadata is not None:
                                usage = normalize_usage(
                                    response.usage_metadata,
                                    usage,
                                    model_id=response.model_version,
                                )
                                yield CallEvent("usage", usage=usage)
                            if (
                                not response.candidates
                                and response.prompt_feedback is None
                                and response.usage_metadata is not None
                            ):
                                continue
                            if terminal is not None:
                                raise InvocationError("invalid_output")
                            event = result(
                                response,
                                usage,
                                streaming=True,
                                displayed=displayed,
                            )
                            usage = event.usage
                            if event.text:
                                if event.text.strip():
                                    displayed = True
                                    first.reschedule(None)
                                yield CallEvent("text_delta", text=event.text)
                            if event.type != "text_delta":
                                terminal = CallEvent(
                                    event.type,
                                    usage=usage,
                                    error_code=event.error_code,
                                    response_received=True,
                                )
                                if event.type != "completed":
                                    break
    except asyncio.CancelledError:
        terminal = CallEvent(
            "error" if deadline.expired() else "interrupted",
            error_code="timeout_total" if deadline.expired() else "interrupted",
        )
    except TimeoutError:
        terminal = CallEvent(
            "error",
            error_code=(
                "timeout_first_output"
                if first is not None and first.expired()
                else "timeout_total"
            ),
        )
    except Exception as error:
        terminal = exception_event(error)
    terminal = terminal or CallEvent("error", error_code="invalid_output")
    yield replace(terminal, usage=usage, response_received=received)


def google_schema(model):
    schema = strict_schema(model)

    def transport_constraints(node):
        # String validation remains authoritative on the server, per Google subset.
        for key in ("minLength", "maxLength", "pattern", "multipleOf"):
            node.pop(key, None)
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                transport_constraints(child)
        if "items" in node:
            transport_constraints(node["items"])
        for child in node.get("anyOf", []):
            transport_constraints(child)

    transport_constraints(schema)
    return schema


async def generate_structured(request, secret, timeouts, *, deadline=None):
    if not isinstance(request, StructuredRequest):
        return CallEvent("error", error_code="configuration_unavailable")
    event = await generate_text(request, secret, timeouts, deadline=deadline)
    if event.type != "completed":
        return event
    try:
        value = validate_output(request, event.text)
    except InvocationError as error:
        return replace(event, type="error", text="", error_code=error.code)
    return replace(event, text="", structured=value)

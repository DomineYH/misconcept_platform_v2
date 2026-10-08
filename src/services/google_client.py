"""Request-scoped Gemini SDK with public HTTPX transport and no SDK retries."""

import logging
import warnings
from contextlib import asynccontextmanager

import httpx
from google.genai import Client, errors, types
from httpx import AsyncClient

from src.services.call_policy import retry_after
from src.services.invocation_types import CallEvent, InvocationError

logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def scoped_client(secret, connect_timeout):
    # Unknown enum warnings interpolate upstream values into stderr.
    warnings.filterwarnings(
        "ignore",
        message=".* is not a valid .*",
        category=UserWarning,
        module="google.genai._common",
    )
    responses = []

    async def timeout_hook(request):
        # The SDK supplies a scalar timeout, overriding AsyncClient's defaults.
        request.extensions["timeout"] = dict(
            connect=connect_timeout, read=None, write=None, pool=None
        )

    async def response_hook(response):
        responses.append(response)

    async with AsyncClient(
        timeout=None,
        event_hooks={"request": [timeout_hook], "response": [response_hook]},
    ) as transport:
        with Client(
            api_key=secret,
            vertexai=False,
            http_options=types.HttpOptions(
                api_version="v1beta",
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_async_client=transport,
            ),
        ) as client:
            try:
                async with client.aio as async_client:
                    yield async_client
            finally:
                # Nested SDK generators do not guarantee response closure on aclose.
                for response in responses:
                    await response.aclose()


def exception_code(error):
    if isinstance(error, InvocationError):
        return error.code
    if isinstance(error, httpx.ConnectTimeout):
        return "timeout_connect"
    if isinstance(error, httpx.TimeoutException):
        return "timeout_total"
    if isinstance(error, httpx.TransportError):
        return "transient"
    if isinstance(error, errors.APIError):
        body = error.details
        if isinstance(body, dict):
            body = body.get("error", body)
        details = body.get("details", []) if isinstance(body, dict) else []
        if (
            error.code == 400
            and isinstance(details, list)
            and any(
                isinstance(item, dict)
                and item.get("@type")
                == "type.googleapis.com/google.rpc.ErrorInfo"
                and item.get("reason") == "API_KEY_INVALID"
                for item in details
            )
        ):
            return "authentication"
        return {
            401: "authentication",
            402: "quota",
            403: "permission",
            404: "model_unavailable",
            408: "transient",
            409: "transient",
            429: "rate_limited",
        }.get(
            error.code,
            (
                "transient"
                if error.code and error.code >= 500
                else "invalid_output"
            ),
        )
    return "invalid_output"


def exception_event(error):
    response = error.response if isinstance(error, errors.APIError) else None
    return CallEvent(
        "error",
        error_code=exception_code(error),
        retry_after_seconds=(
            retry_after(response.headers.get("Retry-After"))
            if response is not None
            else None
        ),
    )

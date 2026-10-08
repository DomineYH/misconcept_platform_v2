"""Safe Claude error taxonomy; never expose SDK bodies or exception strings."""

import httpx2
from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
)

from src.services.invocation_types import InvocationError


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
        detail = body.get("error", body)
        kind = detail.get("type") if isinstance(detail, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else None
        details = detail.get("details") if isinstance(detail, dict) else None
        if (
            isinstance(details, dict)
            and details.get("error_code") == "enforced_spend_limit_reached"
        ):
            return "quota"
        if (
            kind == "invalid_request_error"
            and isinstance(message, str)
            and message.startswith(
                (
                    "You have reached your specified API usage limits",
                    "You have reached your specified workspace API usage limits",
                )
            )
        ):
            return "quota"
        if (
            kind == "invalid_request_error"
            and isinstance(message, str)
            and message.lower().startswith("prompt is too long")
        ):
            return "context_limit"
        code = {
            "authentication_error": "authentication",
            "permission_error": "permission",
            "not_found_error": "model_unavailable",
            "rate_limit_error": "rate_limited",
            "overloaded_error": "transient",
            "api_error": "transient",
            "billing_error": "quota",
        }.get(kind)
        if code:
            return code
        if isinstance(error, APIStatusError):
            return {
                401: "authentication",
                402: "quota",
                403: "permission",
                404: "model_unavailable",
                429: "rate_limited",
                408: "transient",
                409: "transient",
            }.get(
                error.status_code,
                "transient" if error.status_code >= 500 else "invalid_output",
            )
    if isinstance(error, (APIConnectionError, httpx2.TransportError)):
        return "transient"
    if isinstance(error, TimeoutError):
        return "timeout_total"
    return "invalid_output"

"""Request-scoped non-generating OpenAI Models API adapter."""

import json
import logging
from contextlib import nullcontext
from datetime import date

import httpx2
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from src.services.call_policy import CallDeadline
from src.services.model_capabilities import normalize_model_id

# SDK debug traces can contain provider error bodies or HTTP headers.
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


class CatalogError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    created: StrictInt = Field(ge=0)
    owned_by: str
    shutdown_date: date | None = None

    @field_validator("model_id")
    @classmethod
    def valid_id(cls, value):
        return normalize_model_id(value)


def status_code(error):
    if error.status_code == 401:
        return "authentication"
    if error.status_code == 403:
        return "permission"
    if error.status_code == 404:
        return "model_unavailable"
    if error.status_code == 429:
        body = error.body if isinstance(error.body, dict) else {}
        return (
            "quota"
            if body.get("code") == "insufficient_quota"
            else "rate_limited"
        )
    return (
        "transient"
        if error.status_code >= 500 or error.status_code in (408, 409)
        else "invalid_output"
    )


async def list_models(secret, *, connect_timeout, total_timeout, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(
        {"model_list_total": total_timeout}, None
    )
    try:
        async with deadline.total() if owned else nullcontext():
            async with AsyncOpenAI(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(total_timeout, connect=connect_timeout),
            ) as client:
                models = {}
                # The pinned Models API currently returns one unpaginated AsyncPage.
                # Use its public iterator so every SDK page is consumed if supported.
                async for page in (await client.models.list()).iter_pages():
                    if page.object != "list" or not isinstance(page.data, list):
                        raise ValueError("invalid_catalog")
                    for model in page.data:
                        item = CatalogModel(
                            model_id=model.id,
                            created=model.created,
                            owned_by=model.owned_by,
                            shutdown_date=model.shutdown_date,
                        ).model_dump(mode="json")
                        if (
                            secret in json.dumps(item)
                            or item["model_id"] in models
                        ):
                            raise ValueError("invalid_metadata")
                        models[item["model_id"]] = item
                return sorted(
                    models.values(), key=lambda item: item["model_id"]
                )
    except APITimeoutError as error:
        code = (
            "timeout_connect"
            if isinstance(error.__cause__, httpx2.ConnectTimeout)
            else "timeout_total"
        )
        raise CatalogError(code) from None
    except TimeoutError:
        raise CatalogError("timeout_total") from None
    except APIStatusError as error:
        raise CatalogError(status_code(error)) from None
    except APIConnectionError:
        raise CatalogError("transient") from None
    except (APIError, ValueError, TypeError, AttributeError):
        raise CatalogError("invalid_output") from None

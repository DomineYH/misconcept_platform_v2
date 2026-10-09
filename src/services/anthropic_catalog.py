"""Non-generating Claude Models API; all pages, allowlisted public metadata."""

import json
import logging
from contextlib import nullcontext
from datetime import datetime
from typing import Literal

import httpx2
from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from src.services.anthropic_errors import exception_code
from src.services.call_policy import CallDeadline
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import normalize_model_id

logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


class Support(BaseModel):
    supported: StrictBool | None = None


class ThinkingTypes(BaseModel):
    adaptive: Support | None = None
    disabled: Support | None = None
    enabled: Support | None = None


class Thinking(Support):
    types: ThinkingTypes | None = None


class Effort(Support):
    low: Support | None = None
    medium: Support | None = None
    high: Support | None = None
    max: Support | None = None


class CatalogCapabilities(BaseModel):
    structured_outputs: Support | None = None
    thinking: Thinking | None = None
    effort: Effort | None = None


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    display_name: str
    created_at: datetime
    lifecycle: Literal["active", "deprecated", "retired"] | None = None
    max_input_tokens: StrictInt | None = Field(default=None, gt=0)
    max_tokens: StrictInt | None = Field(default=None, gt=0)
    capabilities: CatalogCapabilities | None = None


async def list_models(secret, *, connect_timeout, total_timeout, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(
        {"model_list_total": total_timeout}, None
    )
    try:
        async with deadline.total() if owned else nullcontext():
            async with AsyncAnthropic(
                api_key=secret,
                max_retries=0,
                timeout=httpx2.Timeout(total_timeout, connect=connect_timeout),
            ) as client:
                models = {}
                async for page in (await client.models.list()).iter_pages():
                    if type(page.has_more) is not bool or not isinstance(
                        page.data, list
                    ):
                        raise InvocationError("invalid_output")
                    if page.has_more and (not page.data or not page.last_id):
                        raise InvocationError("invalid_output")
                    for model in page.data:
                        if model.type != "model":
                            raise InvocationError("invalid_output")
                        value = {
                            name: getattr(model, name, None)
                            for name in CatalogModel.model_fields
                            if name != "model_id"
                        }
                        caps = value["capabilities"]
                        value["capabilities"] = (
                            caps.model_dump(warnings=False)
                            if caps is not None
                            else None
                        )
                        item = CatalogModel(
                            model_id=normalize_model_id(model.id), **value
                        ).model_dump(mode="json", exclude_none=True)
                        if (
                            secret in json.dumps(item)
                            or item["model_id"] in models
                        ):
                            raise InvocationError("invalid_output")
                        models[item["model_id"]] = item
                return sorted(
                    models.values(), key=lambda item: item["model_id"]
                )
    except (
        APIError,
        httpx2.TransportError,
        ValueError,
        TypeError,
        AttributeError,
        TimeoutError,
        InvocationError,
    ) as error:
        raise InvocationError(exception_code(error)) from None

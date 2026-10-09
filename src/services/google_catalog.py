"""All Gemini model pages, with an allowlist of public model metadata."""

from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from src.services.call_policy import CallDeadline
from src.services.google_client import exception_code, scoped_client
from src.services.model_capabilities import normalize_model_id
from src.services.openai_catalog import CatalogError


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    display_name: str | None = None
    version: str | None = None
    input_token_limit: StrictInt | None = Field(default=None, gt=0)
    output_token_limit: StrictInt | None = Field(default=None, gt=0)
    supported_actions: list[str] | None = None
    thinking: StrictBool | None = None
    max_temperature: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )


async def list_models(secret, *, connect_timeout, total_timeout, deadline=None):
    owned = deadline is None
    deadline = deadline or CallDeadline(
        {"model_list_total": total_timeout}, None
    )
    try:
        async with deadline.total() if owned else nullcontext():
            async with scoped_client(secret, connect_timeout) as client:
                models = {}
                async for model in await client.models.list():
                    item = CatalogModel(
                        model_id=normalize_model_id(
                            model.name, provider="google"
                        ),
                        **{
                            name: getattr(model, name)
                            for name in CatalogModel.model_fields
                            if name != "model_id"
                        },
                    )
                    models[item.model_id] = item.model_dump(mode="json")
                return sorted(
                    models.values(), key=lambda item: item["model_id"]
                )
    except TimeoutError:
        raise CatalogError("timeout_total") from None
    except Exception as error:
        raise CatalogError(exception_code(error)) from None

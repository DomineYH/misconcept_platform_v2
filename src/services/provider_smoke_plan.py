"""Fixed synthetic calls and conservative, documented billing reservations."""

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import text

from src.models import ModelConfig, ProviderConnection, User
from src.services import anthropic_usage, google_usage, openai_usage
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import (
    capabilities,
    validate_model_and_options,
)
from src.services.model_verification import (
    ROLE_CONTRACT_VERSIONS,
    model_available,
)
from src.services.role_probe_contract import probe_steps


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_config_id: StrictInt = Field(gt=0)
    role: Literal["student", "mentor", "analysis"]
    options: dict


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def synthetic_request(call, request_id):
    # Reuse only fixed inputs, never the probe executor or its multiple steps.
    return probe_steps(
        call["provider"],
        call["model"],
        call["role"],
        call["options"],
        request_id,
    )[0][1:]


def reservation(provider, model, options):
    definition = capabilities(provider, model)
    if definition is None or "max_output_tokens" not in options:
        raise InvocationError("unknown_bound")
    output = options["max_output_tokens"]
    input_limit = definition.get("input_token_limit") or definition.get(
        "combined_context_tokens"
    )
    if not input_limit or not definition.get("limit_sources"):
        raise InvocationError("unknown_bound")
    if provider == "openai":

        def price(input_limit, output):
            return openai_usage.normalize_usage(
                SimpleNamespace(
                    input_tokens=input_limit,
                    output_tokens=output,
                    total_tokens=input_limit + output,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                    output_tokens_details=SimpleNamespace(
                        reasoning_tokens=output
                    ),
                ),
                model_id=model,
                service_tier="default",
            )

        billing = dict(
            service_tier="default",
            cache="uncached maximum",
            reasoning="included in output cap",
        )
    elif provider == "anthropic":

        def price(input_limit, output):
            return anthropic_usage.normalize_usage(
                SimpleNamespace(
                    input_tokens=0,
                    output_tokens=output,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=input_limit,
                    cache_creation=SimpleNamespace(
                        ephemeral_5m_input_tokens=0,
                        ephemeral_1h_input_tokens=input_limit,
                    ),
                    service_tier="standard",
                    inference_geo="global",
                ),
                model_id=model,
            )

        billing = dict(
            service_tier="standard",
            inference_geo="global",
            cache="all input at 1h write maximum",
            reasoning="included in output cap",
        )
    elif provider == "google" and options.get("thinking") == {"budget": 0}:

        def price(input_limit, output):
            return google_usage.normalize_usage(
                SimpleNamespace(
                    prompt_token_count=input_limit,
                    candidates_token_count=output,
                    thoughts_token_count=0,
                    cached_content_token_count=0,
                    total_token_count=input_limit + output,
                ),
                model_id=model,
            )

        billing = dict(
            service_tier="standard native",
            cache="uncached maximum; no cache storage",
            reasoning="disabled by explicit budget 0",
        )
    else:
        raise InvocationError("unknown_bound")
    input_price = price(1_000_000, 0)
    output_price = price(0, 1_000_000)
    if any(
        usage["estimated_cost_usd"] is None
        for usage in (input_price, output_price)
    ):
        raise InvocationError("unknown_price")
    cost = (
        Decimal(str(input_price["estimated_cost_usd"])) * input_limit
        + Decimal(str(output_price["estimated_cost_usd"])) * output
    ) / Decimal(1_000_000)
    return dict(
        input_tokens_upper=input_limit,
        output_tokens_upper=output,
        reasoning_tokens_upper=0 if provider == "google" else output,
        reserved_usd=str(cost.normalize()),
        input_rate_usd_per_million=str(input_price["estimated_cost_usd"]),
        output_rate_usd_per_million=str(output_price["estimated_cost_usd"]),
        pricing_source=input_price["pricing_source"],
        pricing_as_of=input_price["pricing_as_of"],
        limit_sources=definition["limit_sources"],
        limits_checked_at=definition["limits_checked_at"],
        billing=billing,
    )


async def build_plan(factory, selections, owner_id, plan_id):
    if not selections or len(selections) > 9:
        raise InvocationError("invalid_plan")
    calls, seen = [], set()
    async with factory() as db:
        for table in (
            "session",
            "message",
            "scenario",
            "comparison_run",
            "generation_run",
        ):
            if await db.scalar(text(f'SELECT count(*) FROM "{table}"')):
                raise InvocationError("classroom_database_forbidden")
        owner = await db.get(User, owner_id)
        if owner is None or not owner.is_admin:
            raise InvocationError("configuration_unavailable")
        for value in selections:
            selection = Selection.model_validate(value)
            model = await db.get(ModelConfig, selection.model_config_id)
            connection = (
                await db.get(ProviderConnection, model.provider_connection_id)
                if model
                else None
            )
            if connection is None:
                raise InvocationError("configuration_unavailable")
            options = validate_model_and_options(
                connection.provider,
                model.model_id,
                selection.options,
                app_mode="normal",
            )
            if not model_available(
                model, connection, selection.role, options, app_mode="normal"
            ):
                raise InvocationError("configuration_unavailable")
            identity = (connection.provider, selection.role)
            if identity in seen:
                raise InvocationError("duplicate_role")
            seen.add(identity)
            call = dict(
                model_config_id=model.id,
                config_version=model.config_version,
                connection_id=connection.id,
                connection_version=connection.connection_version,
                credential_revision=connection.credential_revision,
                capability_definition_version=model.capability_definition_version,
                role_contract_version=ROLE_CONTRACT_VERSIONS[selection.role],
                provider=connection.provider,
                model=model.model_id,
                role=selection.role,
                options=options,
                **reservation(connection.provider, model.model_id, options),
            )
            kind, request = synthetic_request(call, "planning")
            call.update(
                kind=kind,
                input_sha256=digest(
                    dict(
                        system=request.system_instruction,
                        messages=request.messages,
                        schema=(
                            request.output_schema.model_json_schema()
                            if kind == "structured"
                            else None
                        ),
                        context=(
                            request.validation_context
                            if kind == "structured"
                            else None
                        ),
                    )
                ),
            )
            calls.append(call)
    total = sum((Decimal(call["reserved_usd"]) for call in calls), Decimal(0))
    if total > Decimal(3):
        raise InvocationError("budget_exceeded")
    return dict(
        plan_id=plan_id,
        owner_id=owner_id,
        calls=calls,
        call_count=len(calls),
        retry_count=0,
        probe_count=0,
        split_count=0,
        reserved_usd=str(total),
        maximum_usd="3",
    )

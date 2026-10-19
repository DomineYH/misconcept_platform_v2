"""Exact provider definitions, checked against official sources on 2026-10-13."""

import unicodedata
from copy import deepcopy
from datetime import date
from typing import Literal

from src.i18n import translate
from src.services import anthropic_capabilities, google_capabilities

PROVIDER_CAPABILITIES = {
    "anthropic": anthropic_capabilities,
    "google": google_capabilities,
}

DEFINITION_VERSION = "openai-2026-10-13-v4"
MODEL_PAGES = "https://developers.openai.com/api/docs/models/"
PARAMETER_SOURCE = "https://developers.openai.com/api/docs/guides/latest-model"
MINI_REASONING_SOURCE = (
    "https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5"
)
DEFINITIONS = {
    "gpt-5-mini": (
        ["minimal", "low", "medium", "high"],
        "medium",
        False,
        400000,
        272000,
    ),
    "gpt-5.2": (
        ["none", "low", "medium", "high", "xhigh"],
        "none",
        True,
        400000,
        None,
    ),
    "gpt-5.5": (
        ["none", "low", "medium", "high", "xhigh"],
        "medium",
        True,
        1050000,
        None,
    ),
    "gpt-5.6-sol": (
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        True,
        1050000,
        922000,
    ),
    "gpt-5.6-terra": (
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        True,
        1050000,
        922000,
    ),
    "gpt-6-sol": (
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        True,
        1050000,
        922000,
    ),
    "gpt-6-luna": (
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        True,
        1050000,
        922000,
    ),
    "gpt-6.1-sol": (
        ["low", "medium", "high", "xhigh", "max"],
        "medium",
        False,
        1050000,
        922000,
    ),
    # shortcut: Astra omission default is unverified, fill after official confirmation.
    "gpt-6-astra": (
        ["low", "medium", "high", "xhigh", "max"],
        None,
        False,
        1050000,
        922000,
    ),
}
DEMO_MODEL_ID = "demo-classroom-v1"
DEMO_MODEL_TITLE = "준비된 수업 예시 v1"
# Role default is the minimum; ScenarioConfig snapshots it explicitly.
DEMO_OUTPUT_TOKENS = (4096, 8192)
SNAPSHOTS = {
    "gpt-5-mini-2025-08-07": "gpt-5-mini",
    "gpt-5.2-2025-12-11": "gpt-5.2",
}


def normalize_model_id(value, provider=None):
    value = value.strip()
    if not value or any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("invalid_model_id")
    value.encode("utf-8")
    if provider == "google" and value.startswith("models/"):
        return normalize_model_id(value.removeprefix("models/"))
    return value


def metadata_conflict(model_id, connection):
    definition = capabilities(connection.provider, model_id)
    for item in connection.catalog_models_json:
        if item.get("model_id") != model_id or definition is None:
            continue
        for field, limit in (
            ("input_token_limit", "input_token_limit"),
            ("combined_context_tokens", "combined_context_tokens"),
            ("output_token_limit", "max_output_tokens"),
        ):
            value, maximum = item.get(field), definition.get(limit)
            if value is not None and maximum is not None and value < maximum:
                return True
    if connection.provider == "anthropic":
        return anthropic_capabilities.metadata_conflict(
            model_id, connection.catalog_models_json
        )
    for item in connection.catalog_models_json:
        if connection.provider == "google" and item.get("model_id") == model_id:
            return google_capabilities.metadata_conflict(model_id, item)
        if item.get("model_id") == model_id and item.get("shutdown_date"):
            return date.fromisoformat(item["shutdown_date"]) <= date.today()
    return False


def capabilities(provider, model_id, language="ko"):
    if provider == "demo":
        return demo_capabilities(model_id, language)
    adapter = PROVIDER_CAPABILITIES.get(provider)
    if adapter is not None:
        return adapter.capabilities(model_id, language)
    name = SNAPSHOTS.get(model_id, model_id)
    if provider != "openai" or name not in DEFINITIONS:
        return None
    efforts, default, sampling, context, input_limit = DEFINITIONS[name]
    fields = [
        dict(
            name="max_output_tokens",
            label=translate("admin.maximum_output_tokens", language),
            type="integer",
            min=1,
            max=128000,
        ),
        dict(
            name="reasoning.effort",
            label=translate("admin.reasoning_effort", language),
            type="string",
            choices=efforts,
            default=default,
        ),
    ]
    if sampling:
        fields.append(
            dict(
                name="temperature",
                label=translate("admin.temperature", language),
                type="number",
                min=0,
                max=2,
            )
        )
    return deepcopy(
        dict(
            definition_version=DEFINITION_VERSION,
            checked_at="2026-10-13",
            limits_checked_at="2026-10-13",
            limit_sources={
                k: MODEL_PAGES + name
                for k in (
                    "input_token_limit",
                    "combined_context_tokens",
                    "max_output_tokens",
                )
            },
            sources=(
                [
                    MODEL_PAGES + name,
                    PARAMETER_SOURCE,
                    MINI_REASONING_SOURCE,
                    "https://developers.openai.com/api/docs/guides/reasoning",
                ]
                if name == "gpt-5-mini"
                else [
                    MODEL_PAGES + name,
                    PARAMETER_SOURCE,
                    "https://developers.openai.com/api/docs/guides/reasoning",
                ]
            ),
            text=True,
            streaming=True,
            structured=True,
            max_output_tokens=128000,
            combined_context_tokens=context,
            input_token_limit=input_limit,
            option_notes=[
                (
                    translate(
                        "admin.unspecified_uses_the_provider_default", language
                    )
                    if default is not None
                    else translate(
                        "admin.the_official_default_reasoning_effort_needs_verification_unspecified_omits_the_value_and_does_not_su",
                        language,
                    )
                ),
                (
                    translate(
                        "admin.temperature_is_supported_only_with_reasoning_effort_none",
                        language,
                    )
                    if sampling
                    else translate(
                        "admin.temperature_is_not_supported", language
                    )
                ),
            ],
            reasoning_efforts=efforts,
            default_reasoning_effort=default,
            temperature=sampling,
            fields=fields,
        )
    )


def demo_capabilities(model_id, language):
    if model_id != DEMO_MODEL_ID:
        return None
    minimum, maximum = DEMO_OUTPUT_TOKENS
    # Local estimates for the fixture provider, not provider-reported usage.
    return dict(
        definition_version="demo-cap-v1",
        text=True,
        streaming=True,
        structured=True,
        tools=False,
        image=False,
        audio=False,
        input_token_limit=65536,
        combined_context_tokens=None,
        max_output_tokens=maximum,
        fields=[
            dict(
                name="max_output_tokens",
                label=translate("admin.maximum_output_tokens", language),
                type="integer",
                min=minimum,
                max=maximum,
                default=minimum,
            )
        ],
    )


def validate_model_and_options(
    provider, model_id, options, *, app_mode: Literal["normal", "demo"]
):
    # Demo rows never run in normal and external models never run in demo.
    if (provider == "demo") != (app_mode == "demo"):
        raise ValueError("configuration_unavailable")
    if provider == "demo":
        if capabilities(provider, model_id) is None:
            raise ValueError("capability_definition_required")
        if (
            not isinstance(options, dict)
            or set(options) != {"max_output_tokens"}
            or type(options["max_output_tokens"]) is not int
            or not DEMO_OUTPUT_TOKENS[0]
            <= options["max_output_tokens"]
            <= DEMO_OUTPUT_TOKENS[1]
        ):
            raise ValueError("invalid_options")
        return deepcopy(options)
    adapter = PROVIDER_CAPABILITIES.get(provider)
    if adapter is not None:
        return adapter.validate_model_and_options(model_id, options)
    definition = capabilities(provider, model_id)
    if definition is None:
        raise ValueError("capability_definition_required")
    if not isinstance(options, dict) or set(options) - {
        "max_output_tokens",
        "temperature",
        "reasoning",
    }:
        raise ValueError("invalid_options")
    if "max_output_tokens" in options:
        value = options["max_output_tokens"]
        if (
            type(value) is not int
            or not 1 <= value <= definition["max_output_tokens"]
        ):
            raise ValueError("invalid_options")
    effort = definition["default_reasoning_effort"]
    if "reasoning" in options:
        reasoning = options["reasoning"]
        if (
            not isinstance(reasoning, dict)
            or set(reasoning) != {"effort"}
            or reasoning["effort"] not in definition["reasoning_efforts"]
        ):
            raise ValueError("invalid_options")
        effort = reasoning["effort"]
    if "temperature" in options:
        value = options["temperature"]
        if (
            not definition["temperature"]
            or effort != "none"
            or type(value) not in (int, float)
            or not 0 <= value <= 2
        ):
            raise ValueError("invalid_options")
    return deepcopy(options)

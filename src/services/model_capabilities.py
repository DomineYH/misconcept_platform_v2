"""Exact provider definitions, checked against official sources on 2026-10-09."""

import unicodedata
from copy import deepcopy
from datetime import date

from src.services import anthropic_capabilities, google_capabilities

DEFINITION_VERSION = "openai-2026-10-09-v1"
MODEL_PAGES = "https://developers.openai.com/api/docs/models/"
PARAMETER_SOURCE = (
    "https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4"
)
MINI_REASONING_SOURCE = (
    "https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5"
)
DEFINITIONS = {
    "gpt-5-mini": (["minimal", "low", "medium", "high"], "medium", False),
    "gpt-5.2": (["none", "low", "medium", "high", "xhigh"], "none", True),
}
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


def capabilities(provider, model_id):
    if provider == "anthropic":
        return anthropic_capabilities.capabilities(model_id)
    if provider == "google":
        return google_capabilities.capabilities(model_id)
    name = SNAPSHOTS.get(model_id, model_id)
    if provider != "openai" or name not in DEFINITIONS:
        return None
    efforts, default, sampling = DEFINITIONS[name]
    fields = [
        dict(
            name="max_output_tokens",
            label="최대 출력 토큰",
            type="integer",
            min=1,
            max=128000,
        ),
        dict(
            name="reasoning.effort",
            label="추론 수준",
            type="string",
            choices=efforts,
        ),
    ]
    if sampling:
        fields.append(
            dict(
                name="temperature", label="다양성", type="number", min=0, max=2
            )
        )
    return deepcopy(
        dict(
            definition_version=DEFINITION_VERSION,
            checked_at="2026-10-09",
            sources=(
                [MODEL_PAGES + name, PARAMETER_SOURCE, MINI_REASONING_SOURCE]
                if name == "gpt-5-mini"
                else [MODEL_PAGES + name, PARAMETER_SOURCE]
            ),
            text=True,
            streaming=True,
            structured=True,
            max_output_tokens=128000,
            reasoning_efforts=efforts,
            default_reasoning_effort=default,
            temperature=sampling,
            fields=fields,
        )
    )


def validate_model_and_options(provider, model_id, options):
    if provider == "anthropic":
        return anthropic_capabilities.validate_model_and_options(
            model_id, options
        )
    if provider == "google":
        return google_capabilities.validate_options(model_id, options)
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

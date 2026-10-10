"""Exact Claude definition and native option rules, checked 2026-10-10."""

from copy import deepcopy

MODEL = "claude-sonnet-4-6"
VERSION = "anthropic-2026-10-10-v2"
SOURCES = [
    "https://platform.claude.com/docs/en/models/sonnet-4-6/overview",
    "https://platform.claude.com/docs/en/api/messages/create",
    "https://platform.claude.com/docs/en/build-with-claude/structured-outputs",
    "https://platform.claude.com/docs/en/build-with-claude/effort",
    "https://platform.claude.com/docs/en/claude_api_primer",
    "https://platform.claude.com/cookbook/extended-thinking-extended-thinking",
]


def capabilities(model_id):
    if model_id != MODEL:
        return None
    return dict(
        definition_version=VERSION,
        checked_at="2026-10-10",
        sources=list(SOURCES),
        text=True,
        streaming=True,
        structured=True,
        max_output_tokens=128000,
        combined_context_tokens=1000000,
        temperature=True,
        thinking_types=["disabled", "adaptive", "enabled"],
        efforts=["low", "medium", "high", "max"],
        fields=[
            dict(
                name="max_output_tokens",
                label="최대 출력 토큰",
                type="integer",
                min=1,
                max=128000,
            ),
            dict(
                name="temperature", label="다양성", type="number", min=0, max=1
            ),
            dict(
                name="thinking.type",
                label="Claude thinking 방식",
                type="string",
                choices=["disabled", "adaptive", "enabled"],
            ),
            dict(
                name="thinking.budget_tokens",
                label="Claude thinking 토큰 예산",
                type="integer",
                min=1024,
                max=127999,
            ),
            dict(
                name="output_config.effort",
                label="Claude effort",
                type="string",
                choices=["low", "medium", "high", "max"],
            ),
        ],
    )


def validate_model_and_options(model_id, options):
    definition = capabilities(model_id)
    if definition is None:
        raise ValueError("capability_definition_required")
    if not isinstance(options, dict) or set(options) - {
        "max_output_tokens",
        "temperature",
        "thinking",
        "output_config",
    }:
        raise ValueError("invalid_options")
    maximum = options.get("max_output_tokens", 1024)
    if (
        type(maximum) is not int
        or not 1 <= maximum <= definition["max_output_tokens"]
    ):
        raise ValueError("invalid_options")
    thinking = options.get("thinking", {"type": "disabled"})
    if (
        not isinstance(thinking, dict)
        or thinking.get("type") not in definition["thinking_types"]
    ):
        raise ValueError("invalid_options")
    enabled = thinking["type"] == "enabled"
    if set(thinking) != ({"type", "budget_tokens"} if enabled else {"type"}):
        raise ValueError("invalid_options")
    if enabled:
        budget = thinking["budget_tokens"]
        if type(budget) is not int or not 1024 <= budget < maximum:
            raise ValueError("invalid_options")
    if "temperature" in options:
        value = options["temperature"]
        if (
            type(value) not in (int, float)
            or not 0 <= value <= 1
            or (thinking["type"] != "disabled" and value != 1)
        ):
            raise ValueError("invalid_options")
    if "output_config" in options:
        config = options["output_config"]
        if (
            not isinstance(config, dict)
            or set(config) != {"effort"}
            or config["effort"] not in definition["efforts"]
        ):
            raise ValueError("invalid_options")
    return deepcopy(options)


def metadata_conflict(model_id, items):
    definition = capabilities(model_id)
    if definition is None:
        return False
    for item in items:
        if item.get("model_id") != model_id:
            continue
        if item.get("lifecycle") == "retired":
            return True
        for field, limit in (
            ("max_tokens", "max_output_tokens"),
            ("max_input_tokens", "combined_context_tokens"),
        ):
            value = item.get(field)
            if value is not None and value < definition[limit]:
                return True
        metadata = item.get("capabilities", {})
        promised = [
            metadata.get(name, {})
            for name in ("structured_outputs", "thinking", "effort")
        ]
        promised.extend(
            metadata.get("thinking", {}).get("types", {}).get(name, {})
            for name in definition["thinking_types"]
        )
        promised.extend(
            metadata.get("effort", {}).get(name, {})
            for name in definition["efforts"]
        )
        if any(value.get("supported") is False for value in promised):
            return True
    return False

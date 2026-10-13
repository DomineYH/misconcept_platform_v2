"""Exact Claude definition and native option rules, checked 2026-10-13."""

from copy import deepcopy

MODEL = "claude-sonnet-4-6"
VERSION = "anthropic-2026-10-13-v4"
DEFINITIONS = {
    "claude-sonnet-4-6": (
        ["disabled", "adaptive", "enabled"],
        "disabled",
        ["low", "medium", "high", "max"],
        "high",
    ),
    "claude-opus-5-5": (
        ["adaptive"],
        "adaptive",
        ["low", "medium", "high", "xhigh", "max"],
        "medium",
    ),
    "claude-sonnet-5-5": (
        ["adaptive", "between_tools"],
        "adaptive",
        ["low", "medium", "high", "xhigh", "max"],
        "high",
    ),
    "claude-haiku-5-5": (
        ["disabled", "adaptive"],
        "adaptive",
        ["low", "medium", "high", "xhigh", "max"],
        "medium",
    ),
}
SOURCES = [
    "https://platform.claude.com/docs/en/models/sonnet-4-6/overview",
    "https://platform.claude.com/docs/en/api/messages/create",
    "https://platform.claude.com/docs/en/build-with-claude/structured-outputs",
    "https://platform.claude.com/docs/en/build-with-claude/effort",
    "https://platform.claude.com/docs/en/build-with-claude/thinking",
]


def capabilities(model_id):
    if model_id not in DEFINITIONS:
        return None
    modes, default_thinking, efforts, default_effort = DEFINITIONS[model_id]
    legacy = model_id == MODEL
    sources = [
        f"https://platform.claude.com/docs/en/models/{model_id.removeprefix('claude-')}/overview",
        *SOURCES[1:],
    ]
    definition = dict(
        definition_version=VERSION,
        checked_at="2026-10-13",
        limits_checked_at="2026-10-13",
        limit_sources={
            k: sources[0]
            for k in ("combined_context_tokens", "max_output_tokens")
        },
        sources=sources,
        text=True,
        streaming=True,
        structured=True,
        max_output_tokens=128000,
        combined_context_tokens=1000000,
        temperature=True,
        thinking_types=modes,
        default_thinking_type=default_thinking,
        default_effort=default_effort,
        efforts=efforts,
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
                choices=modes,
                default=default_thinking,
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
                choices=efforts,
                default=default_effort,
            ),
        ],
    )
    if not legacy:
        definition["fields"] = [
            field
            for field in definition["fields"]
            if field["name"] != "thinking.budget_tokens"
        ]
        definition["fields"][1]["min"] = 1
    definition["option_notes"] = [
        "지정하지 않음은 제공자 기본값을 사용합니다.",
        (
            "enabled 수동 budget은 deprecated입니다. budget은 최대 출력 토큰보다 작고 1024 이상이어야 합니다. thinking 사용 시 temperature는 1만 허용합니다."
            if legacy
            else "temperature는 기본값 1만 허용합니다. 수동 thinking budget은 지원하지 않습니다."
        ),
    ]
    if "between_tools" in modes:
        definition["option_notes"].append(
            "between_tools는 low·medium·high effort에서만 지원합니다. disabled는 지원하지 않습니다."
        )
    elif "disabled" in modes and not legacy:
        definition["option_notes"].append(
            "disabled는 low·medium·high effort에서만 지원합니다."
        )
    elif not legacy:
        definition["option_notes"].append(
            "adaptive thinking은 항상 활성입니다. disabled는 지원하지 않습니다."
        )
    return deepcopy(definition)


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
    thinking = options.get(
        "thinking", {"type": definition["default_thinking_type"]}
    )
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
            or (
                (model_id != MODEL or thinking["type"] != "disabled")
                and value != 1
            )
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
    effort = options.get("output_config", {}).get(
        "effort", definition["default_effort"]
    )
    if (
        model_id != MODEL
        and thinking["type"] in ("disabled", "between_tools")
        and effort in ("xhigh", "max")
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

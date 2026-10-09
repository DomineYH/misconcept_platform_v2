"""Exact Gemini definition, checked against official sources on 2026-10-09."""

from copy import deepcopy

DEFINITION = dict(
    definition_version="google-2026-10-09-v1",
    checked_at="2026-10-09",
    sources=[
        "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash",
        "https://ai.google.dev/gemini-api/docs/generate-content/thinking",
        "https://ai.google.dev/api/models",
        "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash",
        "https://ai.google.dev/gemini-api/docs/generate-content/text-generation",
        "https://ai.google.dev/gemini-api/docs/generate-content/structured-output",
    ],
    text=True,
    streaming=True,
    structured=True,
    max_output_tokens=65536,
    temperature=True,
    max_temperature=2,
    thinking=True,
    thinking_levels=[],
    thinking_budget_min=0,
    thinking_budget_max=24576,
    fields=[
        dict(
            name="max_output_tokens",
            label="최대 출력 토큰",
            type="integer",
            min=1,
            max=65536,
        ),
        dict(name="temperature", label="다양성", type="number", min=0, max=2),
        dict(
            name="thinking.budget",
            label="사고 토큰 예산 (-1 자동, 0 끄기)",
            type="integer",
            min=-1,
            max=24576,
        ),
    ],
)


def capabilities(model_id):
    return deepcopy(DEFINITION) if model_id == "gemini-2.5-flash" else None


def validate_model_and_options(model_id, options):
    definition = capabilities(model_id)
    if definition is None:
        raise ValueError("capability_definition_required")
    if not isinstance(options, dict) or options.keys() - {
        "max_output_tokens",
        "temperature",
        "thinking",
    }:
        raise ValueError("invalid_options")
    if "max_output_tokens" in options:
        value = options["max_output_tokens"]
        if (
            type(value) is not int
            or not 1 <= value <= definition["max_output_tokens"]
        ):
            raise ValueError("invalid_options")
    if "temperature" in options:
        value = options["temperature"]
        if (
            type(value) not in (int, float)
            or not 0 <= value <= definition["max_temperature"]
        ):
            raise ValueError("invalid_options")
    if "thinking" in options:
        value = options["thinking"]
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("invalid_options")
        if "level" in value:
            if value["level"] not in definition["thinking_levels"]:
                raise ValueError("invalid_options")
        elif "budget" in value:
            budget = value["budget"]
            if type(budget) is not int or (
                budget != -1
                and not definition["thinking_budget_min"]
                <= budget
                <= definition["thinking_budget_max"]
            ):
                raise ValueError("invalid_options")
        else:
            raise ValueError("invalid_options")
    return deepcopy(options)


def metadata_conflict(model_id, item):
    definition = capabilities(model_id)
    if definition is None:
        return False
    return bool(
        (
            item.get("thinking") is not None
            and item["thinking"] != definition["thinking"]
        )
        or (
            item.get("supported_actions") is not None
            and "generateContent" not in item["supported_actions"]
        )
        or (
            item.get("output_token_limit") is not None
            and item["output_token_limit"] != definition["max_output_tokens"]
        )
        or (
            item.get("max_temperature") is not None
            and item["max_temperature"] != definition["max_temperature"]
        )
    )

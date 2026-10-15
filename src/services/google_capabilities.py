"""Exact Gemini definition, checked against official sources on 2026-10-13."""

from copy import deepcopy

from src.i18n import translate

LEVELS = {
    "gemini-3.8-flash": (["low", "medium", "high"], "medium"),
    "gemini-3.6-flash": (["minimal", "low", "medium", "high"], "medium"),
    "gemini-3.5-flash-lite": (["minimal", "low", "medium", "high"], "minimal"),
    "gemini-3.1-flash-lite": (["minimal", "low", "medium", "high"], "minimal"),
    "gemini-3.1-pro-preview": (["low", "medium", "high"], "high"),
}
DEFINITION = dict(
    definition_version="google-2026-10-13-v4",
    checked_at="2026-10-13",
    limits_checked_at="2026-10-13",
    limit_sources={
        k: "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash"
        for k in ("input_token_limit", "max_output_tokens")
    },
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
    input_token_limit=1048576,
    temperature=True,
    max_temperature=2,
    thinking=True,
    thinking_levels=[],
    thinking_budget_min=0,
    thinking_budget_max=24576,
    default_thinking_budget=-1,
    preview=False,
    option_notes=[
        "지정하지 않음은 동적 thinking입니다. -1 자동, 0 끄기, 1–24576 숫자 budget을 구분합니다. level은 지원하지 않습니다."
    ],
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
            default=-1,
        ),
    ],
)


def capabilities(model_id, language="ko"):
    def localized(definition):
        labels = {
            "max_output_tokens": "maximum_output_tokens",
            "temperature": "temperature",
            "thinking.budget": "thinking_token_budget_1_auto_0_off",
            "thinking.level": "thinking_level",
        }
        for field in definition["fields"]:
            field["label"] = translate(
                "admin." + labels[field["name"]], language
            )
        notes = (
            [
                "unspecified_uses_dynamic_thinking_1_is_automatic_0_is_off_and_1_24576_is_a_numeric_budget_level_is_u"
            ]
            if model_id == "gemini-2.5-flash"
            else [
                "unspecified_uses_the_provider_default_level_level_and_budget_cannot_be_combined_this_app_edits_gemin",
                (
                    "minimal_does_not_guarantee_that_thinking_is_fully_disabled"
                    if "minimal" in definition["thinking_levels"]
                    else "minimal_is_unsupported"
                ),
            ]
        )
        definition["option_notes"] = [
            translate("admin." + key, language) for key in notes
        ]
        return definition

    if model_id == "gemini-2.5-flash":
        return localized(deepcopy(DEFINITION))
    if model_id not in LEVELS:
        return None
    levels, default = LEVELS[model_id]
    definition = deepcopy(DEFINITION)
    source = f"https://ai.google.dev/gemini-api/docs/models/{model_id}"
    definition["sources"][0] = source
    definition["limit_sources"] = {
        key: source for key in definition["limit_sources"]
    }
    for key in (
        "thinking_budget_min",
        "thinking_budget_max",
        "default_thinking_budget",
    ):
        del definition[key]
    definition.update(
        thinking_levels=list(levels),
        default_thinking_level=default,
        preview=model_id == "gemini-3.1-pro-preview",
    )
    definition["fields"][-1] = dict(
        name="thinking.level",
        label="사고 수준",
        type="string",
        choices=list(levels),
        default=default,
    )
    definition["option_notes"] = [
        "지정하지 않음은 제공자 기본 level을 사용합니다. level과 budget은 함께 지정할 수 없습니다. 이 앱의 Gemini 3 옵션 편집은 level을 사용합니다. 기존 budget 저장 값은 보존하며 명시적 수정이 필요합니다.",
        (
            "minimal은 사고의 완전한 비활성화를 보장하지 않습니다."
            if "minimal" in levels
            else "minimal은 지원하지 않습니다."
        ),
    ]
    return localized(definition)


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
            if (
                "thinking_budget_max" not in definition
                or type(budget) is not int
                or (
                    budget != -1
                    and not definition["thinking_budget_min"]
                    <= budget
                    <= definition["thinking_budget_max"]
                )
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
            item.get("input_token_limit") is not None
            and item["input_token_limit"] != definition["input_token_limit"]
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

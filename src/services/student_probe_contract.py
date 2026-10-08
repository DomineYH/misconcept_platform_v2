"""Fixed synthetic student text/stream contract; no classroom data."""

from src.services.model_capabilities import (
    capabilities,
    validate_model_and_options,
)

SYSTEM_INSTRUCTION = "학생 역할의 짧은 응답 형식 시험입니다. 한국어 존댓말 한 문장으로만 답하세요."
MESSAGES = [
    {
        "role": "user",
        "content": "물은 고체, 액체, 기체 상태가 될 수 있나요? 짧게 답하세요.",
    }
]
OUTPUT_BUDGET = 1024


def probe_options(provider, model, *, output_budget=OUTPUT_BUDGET):
    definition = capabilities(provider, model.model_id)
    options = validate_model_and_options(
        provider, model.model_id, model.default_options_json
    )
    options["max_output_tokens"] = min(
        output_budget,
        options.get("max_output_tokens", definition["max_output_tokens"]),
    )
    return validate_model_and_options(provider, model.model_id, options)

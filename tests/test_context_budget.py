"""Local context policy at the public request boundary."""

import pytest

from src.services.model_capabilities import capabilities


@pytest.mark.parametrize(
    "provider,model,field,capacity",
    [
        ("openai", "gpt-5-mini", "combined_context_tokens", 400000),
        ("openai", "gpt-5.2", "combined_context_tokens", 400000),
        ("openai", "gpt-5-mini-2025-08-07", "combined_context_tokens", 400000),
        ("openai", "gpt-5.2-2025-12-11", "combined_context_tokens", 400000),
        ("anthropic", "claude-sonnet-4-6", "combined_context_tokens", 1000000),
        ("google", "gemini-2.5-flash", "input_token_limit", 1048576),
    ],
)
def test_exact_capacity_has_official_provenance(
    provider, model, field, capacity
):
    definition = capabilities(provider, model)
    assert definition[field] == capacity
    assert definition["checked_at"] == "2026-10-10"
    assert definition["sources"]
    if provider == "google":
        assert "combined_context_tokens" not in definition
    assert capabilities(provider, model + "-unknown") is None


def test_utf8_estimate_counts_literal_korean_emoji_and_braces():
    from src.services.context_budget import estimate_input
    from src.services.invocation_types import TextRequest

    request = TextRequest(
        "openai",
        "gpt-5-mini",
        "student",
        "역할 {x}🙂",
        [{"role": "user", "content": "Hi 한🙂"}],
        {},
        "request",
    )
    assert estimate_input(request) == 1147


@pytest.mark.parametrize(
    "provider,model,options,reserve,budget",
    [
        ("openai", "gpt-5-mini", {}, 128000, 272000),
        (
            "openai",
            "gpt-5.2",
            {"reasoning": {"effort": "high"}},
            128000,
            272000,
        ),
        ("openai", "gpt-5-mini", {"max_output_tokens": 1500}, 1500, 398500),
        ("anthropic", "claude-sonnet-4-6", {}, 1024, 998976),
        (
            "anthropic",
            "claude-sonnet-4-6",
            {
                "max_output_tokens": 2048,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
            },
            2048,
            997952,
        ),
        ("google", "gemini-2.5-flash", {}, 65536, 1048576),
        (
            "google",
            "gemini-2.5-flash",
            {"max_output_tokens": 100, "thinking": {"budget": -1}},
            100,
            1048576,
        ),
    ],
)
def test_reserve_matches_effective_adapter_output_without_changing_options(
    provider, model, options, reserve, budget
):
    from src.services.context_budget import fit_context, input_budget
    from src.services.invocation_types import TextRequest

    request = TextRequest(
        provider,
        model,
        "student",
        "Instruction",
        [{"role": "user", "content": "Why?"}],
        options,
        "request",
    )
    assert input_budget(request) == (
        budget,
        reserve,
        capabilities(provider, model)["definition_version"],
    )
    fitted = fit_context(
        request, prior_pair_count=0, configured_prior_turn_limit=10
    )
    evidence = fitted.context_budget_json
    assert evidence["reserved_output_tokens"] == reserve
    assert evidence["input_budget_tokens"] == budget
    assert (
        evidence["selected_prior_pairs"] == evidence["dropped_prior_pairs"] == 0
    )
    assert fitted.validated_options == options == request.validated_options
    assert fitted.messages == request.messages


@pytest.mark.parametrize("prior_pairs", [0, 1, 100])
@pytest.mark.parametrize("margin", [0, -1])
def test_exact_boundary_drops_only_whole_oldest_pairs(
    monkeypatch, prior_pairs, margin
):
    from src.services import context_budget
    from src.services.invocation_types import InvocationError, TextRequest

    definition = capabilities("openai", "gpt-5-mini")
    definition["combined_context_tokens"] = 1247 + margin
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    history = [
        message
        for index in range(prior_pairs)
        for message in (
            {"role": "user", "content": f"Old question {index}"},
            {"role": "assistant", "content": f"Old answer {index}"},
        )
    ]
    current = {"role": "user", "content": "Hi 한🙂"}
    request = TextRequest(
        "openai",
        "gpt-5-mini",
        "student",
        "역할 {x}🙂",
        history + [current],
        {"max_output_tokens": 100},
        "request",
    )
    if margin < 0:
        with pytest.raises(InvocationError, match="context_limit"):
            context_budget.fit_context(
                request,
                prior_pair_count=prior_pairs,
                configured_prior_turn_limit=100,
            )
        assert request.messages == history + [current]
    else:
        fitted = context_budget.fit_context(
            request,
            prior_pair_count=prior_pairs,
            configured_prior_turn_limit=100,
        )
        assert fitted.messages == [current]
        assert fitted.system_instruction == request.system_instruction
        assert fitted.validated_options == {"max_output_tokens": 100}
        assert fitted.context_budget_json == dict(
            estimator="utf8-v1",
            estimated_input_tokens=1147,
            input_budget_tokens=1147,
            reserved_output_tokens=100,
            configured_prior_turn_limit=100,
            selected_prior_pairs=prior_pairs,
            kept_prior_pairs=0,
            dropped_prior_pairs=prior_pairs,
            target_pair_included=False,
            capability_definition_version=definition["definition_version"],
        )


@pytest.mark.parametrize(
    "limits,expected",
    [
        ({"input_token_limit": 1200, "combined_context_tokens": 5000}, 1200),
        ({"input_token_limit": 5000, "combined_context_tokens": 1300}, 1200),
        ({}, None),
        ({"combined_context_tokens": 100}, None),
        ({"input_token_limit": 0}, None),
    ],
)
def test_min_known_capacity_and_unavailable_budget(
    monkeypatch, limits, expected
):
    from src.services import context_budget
    from src.services.invocation_types import InvocationError, TextRequest

    definition = capabilities("openai", "gpt-5-mini")
    definition.pop("combined_context_tokens")
    definition.update(limits)
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    request = TextRequest(
        "openai",
        "gpt-5-mini",
        "student",
        "Role",
        [{"role": "user", "content": "x"}],
        {"max_output_tokens": 100},
        "r",
    )
    if expected is None:
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            context_budget.fit_context(
                request, prior_pair_count=0, configured_prior_turn_limit=1
            )
    else:
        fitted = context_budget.fit_context(
            request, prior_pair_count=0, configured_prior_turn_limit=1
        )
        assert fitted.context_budget_json["input_budget_tokens"] == expected


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-5-mini"),
        ("anthropic", "claude-sonnet-4-6"),
        ("google", "gemini-2.5-flash"),
    ],
)
def test_structured_budget_uses_native_schema_and_preserves_target_pair(
    monkeypatch, provider, model
):
    import json
    from dataclasses import replace

    from pydantic import BaseModel, ConfigDict, Field

    from src.services import context_budget
    from src.services.invocation_types import StructuredRequest, TextRequest

    class Decision(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        feedback: str = Field(
            max_length=4000, description="Schema instruction " + "한🙂" * 1000
        )

    target = [
        {"role": "user", "content": "Target 한🙂 {x}"},
        {"role": "assistant", "content": "Target answer"},
    ]
    request = StructuredRequest(
        provider,
        model,
        "mentor",
        "Required role",
        [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
        ]
        + target,
        {"max_output_tokens": 1024},
        "r",
        Decision,
    )
    schema = context_budget.provider_schema(request)
    assert schema["properties"]["feedback"]["type"] == "string"
    assert ("maxLength" in schema["properties"]["feedback"]) == (
        provider == "openai"
    )
    protected = replace(request, messages=target)
    expected_envelope = dict(
        system_instruction="Required role",
        messages=target,
        output_schema=schema,
    )
    expected = (
        len(
            json.dumps(
                expected_envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        + 64
        + 1024
    )
    assert context_budget.estimate_input(protected) == expected
    plain = TextRequest(
        provider, model, "mentor", "Required role", target, {}, "r"
    )
    assert context_budget.estimate_input(
        protected
    ) > context_budget.estimate_input(plain)
    definition = capabilities(provider, model)
    definition.pop("combined_context_tokens", None)
    definition["input_token_limit"] = expected
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    fitted = context_budget.fit_context(
        request,
        prior_pair_count=1,
        configured_prior_turn_limit=1,
        target_pair_included=True,
    )
    assert fitted.messages == target
    assert fitted.system_instruction == request.system_instruction
    assert fitted.output_schema is Decision
    assert fitted.context_budget_json["target_pair_included"] is True
    assert fitted.context_budget_json["dropped_prior_pairs"] == 1

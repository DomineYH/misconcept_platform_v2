"""Exact Gemini options through the agreed adapter contract."""

from types import SimpleNamespace

import pytest

from src.services.model_capabilities import (
    capabilities,
    metadata_conflict,
    validate_model_and_options,
)


@pytest.mark.parametrize(
    "limit,conflict", [(None, False), (1048576, False), (1024, True)]
)
def test_catalog_input_capacity_conflict_blocks_google_definition(
    limit, conflict
):
    connection = SimpleNamespace(
        provider="google",
        catalog_models_json=[
            {"model_id": "gemini-2.5-flash", "input_token_limit": limit}
        ],
    )
    assert metadata_conflict("gemini-2.5-flash", connection) is conflict


def test_stable_google_definition_preserves_budget_zero_and_rejects_level():
    definition = capabilities("google", "gemini-2.5-flash")
    assert (
        definition
        and definition["definition_version"] == "google-2026-10-11-v3"
    )
    assert (
        definition["text"]
        and definition["streaming"]
        and definition["structured"]
    )
    assert definition["max_output_tokens"] == 65536
    options = {
        "max_output_tokens": 100,
        "temperature": 0,
        "thinking": {"budget": 0},
    }
    assert (
        validate_model_and_options("google", "gemini-2.5-flash", options)
        == options
    )
    assert validate_model_and_options(
        "google", "gemini-2.5-flash", {"thinking": {"budget": -1}}
    ) == {"thinking": {"budget": -1}}
    for options in [
        {"thinking": {"level": "low"}},
        {"thinking": {"budget": 0, "level": "low"}},
        {"thinking": {"budget": True}},
        {"thinking": {"budget": -2}},
        {"thinking": {"budget": 24577}},
        {"temperature": 2.1},
        {"temperature": True},
        {"reasoning": {"effort": "low"}},
        {"max_output_tokens": 65537},
        {"response_format": {}},
    ]:
        with pytest.raises(ValueError, match="invalid_options"):
            validate_model_and_options("google", "gemini-2.5-flash", options)
    assert capabilities("google", "gemini-custom") is None


@pytest.mark.parametrize(
    "metadata",
    [
        {"thinking": False},
        {"output_token_limit": 8192},
        {"max_temperature": 1},
        {"supported_actions": ["embedContent"]},
    ],
)
def test_explicit_google_metadata_conflicts_block_execution(metadata):
    connection = SimpleNamespace(
        provider="google",
        catalog_models_json=[{"model_id": "gemini-2.5-flash", **metadata}],
    )
    assert metadata_conflict("gemini-2.5-flash", connection)
    connection.catalog_models_json = []
    assert not metadata_conflict("gemini-2.5-flash", connection)

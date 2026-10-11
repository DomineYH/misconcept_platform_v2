"""Shared capacity provenance, catalog conflicts and stale role evidence."""

from types import SimpleNamespace

import pytest

from src.services.model_capabilities import capabilities, metadata_conflict


@pytest.mark.parametrize(
    "provider,model,field",
    [
        ("openai", "gpt-5-mini", "combined_context_tokens"),
        ("anthropic", "claude-sonnet-4-6", "combined_context_tokens"),
        ("google", "gemini-2.5-flash", "input_token_limit"),
    ],
)
def test_shared_limits_record_official_source_and_check_date(
    provider, model, field
):
    definition = capabilities(provider, model)
    assert definition["limit_sources"][field].startswith("https://")
    assert definition["limits_checked_at"] == "2026-10-11"
    assert definition["definition_version"].endswith("-v3")
    assert capabilities(provider, model + "-unknown") is None


def test_smaller_openai_catalog_capacity_blocks_but_unknown_stays_unknown():
    connection = SimpleNamespace(
        provider="openai",
        catalog_models_json=[
            dict(model_id="gpt-5.2", combined_context_tokens=200000)
        ],
    )
    assert metadata_conflict("gpt-5.2", connection)
    connection.catalog_models_json = [dict(model_id="gpt-5.2")]
    assert not metadata_conflict("gpt-5.2", connection)
    connection.catalog_models_json = [
        dict(model_id="gpt-5.2", combined_context_tokens=500000)
    ]
    assert not metadata_conflict("gpt-5.2", connection)


@pytest.mark.parametrize("role", ["student", "mentor", "analysis"])
def test_shared_definition_change_stales_every_role(monkeypatch, role):
    from src.services import model_verification

    monkeypatch.setattr(model_verification, "connection_ready", lambda _: True)
    connection = SimpleNamespace(
        provider="openai", credential_revision=1, connection_version=1
    )
    old = "openai-2026-10-10-v2"
    model = SimpleNamespace(
        model_id="gpt-5-mini",
        capability_definition_version=old,
        verification_state={
            role: dict(
                status="succeeded",
                credential_revision=1,
                connection_version=1,
                capability_definition_version=old,
                role_contract_version=model_verification.ROLE_CONTRACT_VERSIONS[
                    role
                ],
            )
        },
    )
    assert (
        model_verification.effective_roles(model, connection)[role]["status"]
        == "stale"
    )

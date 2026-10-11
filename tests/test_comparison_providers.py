"""Full common history through the three pinned SDK mock transports."""

import pytest
from s2_integration_fixtures import workflow_transport
from sqlalchemy import select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_provider_connections import KEY
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog, ModelConfig, ProviderConnection
from src.services.call_admission import active_calls, registered_calls
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_comparison_sends_full_history_and_exact_options(
    data, client, student, comparison, monkeypatch, provider
):
    connection, model = student.connection, student.model
    connection.provider = provider
    model.model_id = {
        "openai": "gpt-5-mini",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-2.5-flash",
    }[provider]
    connection.encrypted_key, connection.nonce = encrypt_key(connection, KEY, 1)
    definition = capabilities(provider, model.model_id)["definition_version"]
    model.capability_definition_version = definition
    model.verification_state = {
        "student": dict(
            status="succeeded",
            credential_revision=1,
            connection_version=connection.connection_version,
            capability_definition_version=definition,
            role_contract_version="s1-v1",
        )
    }
    options = {
        "openai": dict(max_output_tokens=1024, reasoning={"effort": "low"}),
        "anthropic": dict(
            max_output_tokens=1024,
            thinking={"type": "disabled"},
            temperature=0.4,
        ),
        "google": dict(
            max_output_tokens=1024, thinking={"budget": 0}, temperature=0.4
        ),
    }[provider]
    model.default_options_json = {"max_output_tokens": 64}
    await data.db.commit()
    comparison["panels"][0]["model_config"].update(
        provider=provider, model_id=model.model_id, options=options
    )
    comparison["source_snapshot"]["config"]["runtime"]["context_turn_limit"] = 1
    comparison["canonical_context"].extend(
        [
            dict(role="teacher", content="Second question"),
            dict(role="student", content="Second answer"),
        ]
    )
    mocked = workflow_transport(monkeypatch, provider, budget=1024)
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    assert frames(result)[-1][0] == "output.completed", result.text
    assert len(mocked.calls) == 1
    payload = mocked.payloads[0]
    messages = payload[
        (
            "input"
            if provider == "openai"
            else "messages" if provider == "anthropic" else "contents"
        )
    ]
    contents = [
        (
            message["content"]
            if provider != "google"
            else "".join(part["text"] for part in message["parts"])
        )
        for message in messages
    ]
    assert contents == [
        "Earlier question",
        "Earlier answer",
        "Second question",
        "Second answer",
        "Current question",
    ]
    assert "PRIVATE ANALYSIS" not in str(
        payload
    ) and "PRIVATE MENTOR" not in str(payload)
    state = (
        await client.get(
            f"/admin/comparisons/{frames(result)[0][1]['comparison_run_id']}"
        )
    ).json()
    assert state["panels"][0]["model_config"]["options"] == options
    assert state["panels"][0]["capability_version"] == definition
    expected_applied = {
        "openai": options,
        "anthropic": dict(
            max_tokens=1024,
            thinking={"type": "disabled"},
            extra_body={"temperature": 0.4},
        ),
        "google": dict(
            max_output_tokens=1024,
            temperature=0.4,
            thinking_config={"thinking_budget": 0},
        ),
    }[provider]
    assert state["panels"][0]["applied_options"] == expected_applied
    assert all(
        sdk.is_closed if provider == "google" else sdk.is_closed()
        for sdk in mocked.clients
    )


async def test_unsaved_draft_runs_three_providers_with_same_context_and_no_classroom_rows(
    data, client, student, comparison, monkeypatch
):
    from test_comparison_student import classroom_counts

    before = await classroom_counts(data.db)
    comparison["panels"] = []
    transports = {}
    for index, (provider, model_id, options) in enumerate(
        (
            ("openai", "gpt-5-mini", {"reasoning": {"effort": "low"}}),
            (
                "anthropic",
                "claude-sonnet-4-6",
                {"thinking": {"type": "disabled"}},
            ),
            ("google", "gemini-2.5-flash", {"thinking": {"budget": 0}}),
        ),
        start=1,
    ):
        connection = (
            student.connection
            if provider == "openai"
            else ProviderConnection(provider=provider, credential_revision=1)
        )
        data.db.add(connection)
        await data.db.flush()
        connection.encrypted_key, connection.nonce = encrypt_key(
            connection, KEY, 1
        )
        connection.encryption_key_version = "test-v1"
        connection.masked_hint = "mock"
        connection.enabled = True
        version = capabilities(provider, model_id)["definition_version"]
        model = (
            student.model
            if provider == "openai"
            else ModelConfig(
                provider_connection_id=connection.id,
                model_id=model_id,
                display_name=provider,
            )
        )
        model.enabled = True
        model.capability_definition_version = version
        model.verification_state = {
            "student": dict(
                status="succeeded",
                credential_revision=1,
                connection_version=connection.connection_version,
                capability_definition_version=version,
                role_contract_version="s1-v1",
            )
        }
        data.db.add(model)
        await data.db.flush()
        comparison["panels"].append(
            dict(
                panel_id=str(index),
                model_config=dict(
                    model_config_id=model.id,
                    provider_connection_id=connection.id,
                    provider=provider,
                    model_id=model_id,
                    options={"max_output_tokens": 1024, **options},
                ),
            )
        )
        transports[provider] = workflow_transport(monkeypatch, provider)
        transports[provider].state.student_answer = f"Answer from {provider}"
    await data.db.commit()
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    assert result.status_code == 200, result.text
    accepted = [
        value for kind, value in frames(result) if kind == "run.accepted"
    ]
    assert (
        len(accepted) == 3
        and len({item["context_hash"] for item in accepted}) == 1
    )
    assert all(
        item["source_snapshot"]["scenario_id"] is None for item in accepted
    )
    state = (
        await client.get(
            f"/admin/comparisons/{accepted[0]['comparison_run_id']}"
        )
    ).json()
    assert [panel["status"] for panel in state["panels"]] == ["completed"] * 3
    for panel in state["panels"]:
        provider = panel["model_config"]["provider"]
        assert panel["text"] == f"Answer from {provider}"
        mocked = transports[provider]
        assert len(mocked.calls) == 1 and mocked.schemas == [{}]
        payload = mocked.payloads[0]
        messages = payload[
            {"openai": "input", "anthropic": "messages", "google": "contents"}[
                provider
            ]
        ]
        assert [
            (
                "".join(part["text"] for part in message["parts"])
                if provider == "google"
                else message["content"]
            )
            for message in messages
        ] == ["Earlier question", "Earlier answer", "Current question"]
        assert "Answer from" not in str(mocked.payloads)
        assert "PRIVATE ANALYSIS" not in str(mocked.payloads)
        assert "PRIVATE MENTOR" not in str(mocked.payloads)
        assert all(
            sdk.is_closed if provider == "google" else sdk.is_closed()
            for sdk in mocked.clients
        )
    async with data.factory() as db:
        assert await classroom_counts(db) == before
        ledger = (await db.scalars(select(ApiUsageLog))).all()
        assert len(ledger) == 3
        assert all(
            row.session_id is None and row.usage_scope == "comparison"
            for row in ledger
        )
    assert not active_calls and not registered_calls

"""Full mentor context through each pinned provider's SDK mock transport."""

import pytest
from s2_integration_fixtures import workflow_transport
from test_comparison_mentor import mentor_comparison
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_provider_connections import KEY
from test_scenario_api import login
from test_student_generation import frames

from src.models import ModelConfig
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key

__all__ = [
    "client",
    "comparison",
    "scenario_payload",
    "student",
    "mentor_comparison",
]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_mentor_comparison_preserves_full_context_and_frozen_options(
    data, client, student, mentor_comparison, monkeypatch, provider
):
    selection = mentor_comparison["panels"][0]["model_config"]
    model = (
        student.model
        if provider == "openai"
        else await data.db.get(ModelConfig, selection["model_config_id"])
    )
    selection["model_config_id"] = model.id
    connection = student.connection
    connection.provider = provider
    model.model_id = {
        "openai": "gpt-5-mini",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-2.5-flash",
    }[provider]
    connection.encrypted_key, connection.nonce = encrypt_key(connection, KEY, 1)
    version = capabilities(provider, model.model_id)["definition_version"]
    model.capability_definition_version = version
    model.verification_state = {
        "mentor": dict(
            status="succeeded",
            credential_revision=1,
            connection_version=connection.connection_version,
            capability_definition_version=version,
            role_contract_version="s3-v1",
        )
    }
    model.default_options_json = {"max_output_tokens": 64}
    await data.db.commit()
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
    selection.update(
        provider=provider, model_id=model.model_id, options=options
    )
    mocked = workflow_transport(monkeypatch, provider, budget=1024)
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[-1][1]["result"] == dict(
        should_intervene=True, coaching="Mentor coaching"
    )
    assert len(mocked.calls) == 1
    payload = mocked.payloads[0]
    messages = payload[
        (
            "input"
            if provider == "openai"
            else "messages" if provider == "anthropic" else "contents"
        )
    ]
    contents = (
        messages[0]["content"]
        if provider != "google"
        else "".join(part["text"] for part in messages[0]["parts"])
    )
    assert (
        contents
        == "teacher: Earlier question\nstudent: Earlier answer\nteacher: Middle question\nstudent: Middle answer\nteacher: Target question\nstudent: Target answer"
    )
    assert "PRIVATE ANALYSIS" not in str(payload)
    assert "reason_summary" in mocked.schemas[0]
    state = (
        await client.get(
            f"/admin/comparisons/{events[0][1]['comparison_run_id']}"
        )
    ).json()
    assert state["panels"][0]["model_config"]["options"] == options
    assert (
        state["panels"][0]["applied_options"]
        == {
            "openai": options,
            "anthropic": dict(
                max_tokens=1024,
                thinking={"type": "disabled"},
                extra_body={"temperature": 0.4},
            ),
            "google": dict(
                max_output_tokens=1024,
                thinking_config={"thinking_budget": 0},
                temperature=0.4,
            ),
        }[provider]
    )
    assert '"reason_summary"' not in response.text
    assert "Authored condition" not in response.text + str(state)
    assert all(
        sdk.is_closed if provider == "google" else sdk.is_closed()
        for sdk in mocked.clients
    )

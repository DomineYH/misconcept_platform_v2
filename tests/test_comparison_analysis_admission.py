"""All-panel single-call budgets and provider contracts at the HTTP seam."""

import copy

import pytest
from sqlalchemy import func, select
from test_comparison_analysis import (
    analysis_comparison,
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login

from src.models import (
    ApiUsageLog,
    AppSetting,
    ComparisonRun,
    GenerationRun,
    ModelConfig,
)
from src.services.call_admission import active_calls, registered_calls

__all__ = [
    "analysis_comparison",
    "client",
    "comparison",
    "scenario_payload",
    "student",
]


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("input", 422),
        ("output", 422),
        ("thinking", 422),
        ("missing_cap", 422),
        ("options", 422),
        ("unknown", 503),
        ("verification", 503),
        ("slots", 429),
        ("empty", 422),
        ("many_messages", 422),
        ("analysis_context", 422),
        ("rubric", 422),
    ],
)
async def test_one_unfit_analysis_panel_refuses_every_call_and_comparison_record(
    data, client, student, analysis_comparison, fault, expected
):
    body = analysis_comparison
    body["panels"].append({**copy.deepcopy(body["panels"][0]), "panel_id": "2"})
    selection = body["panels"][1]["model_config"]
    if fault == "input":
        body["canonical_context"] = [
            dict(
                role="teacher" if i % 2 == 0 else "student",
                content="길" * 50000,
            )
            for i in range(6)
        ]
    elif fault == "output":
        selection["options"]["max_output_tokens"] = 1500
    elif fault == "thinking":
        model = ModelConfig(
            provider_connection_id=student.connection.id,
            model_id="gpt-5.2",
            display_name="Thinking analysis",
            enabled=True,
            capability_definition_version=student.model.capability_definition_version,
            verification_state=student.model.verification_state,
        )
        data.db.add(model)
        await data.db.flush()
        selection.update(
            model_config_id=model.id,
            model_id=model.model_id,
            options=dict(max_output_tokens=3000, reasoning=dict(effort="low")),
        )
    elif fault == "missing_cap":
        selection["options"] = {}
    elif fault == "options":
        selection["options"]["temperature"] = 0.3
    elif fault == "unknown":
        student.model.model_id = selection["model_id"] = "unknown-model"
        body["panels"][0]["model_config"]["model_id"] = "unknown-model"
    elif fault == "verification":
        student.model.verification_state = {
            "analysis": {"status": "unverified"}
        }
    elif fault == "slots":
        setting = await data.db.get(AppSetting, 1)
        setting.limits_json = {**setting.limits_json, "admin": 1}
    elif fault == "empty":
        body.update(canonical_context=[], current_message="")
    elif fault == "many_messages":
        body["canonical_context"] *= 100
    elif fault == "analysis_context":
        body["source_snapshot"]["config"]["analysis"][
            "expected_understanding"
        ] = ""
    elif fault == "rubric":
        body["source_snapshot"]["config"]["analysis"]["rubric"][0][
            "id"
        ] = "invalid rubric"
    await data.db.commit()
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=body)
    assert response.status_code == expected, response.text
    if fault in {"output", "thinking", "missing_cap", "options"}:
        assert response.json()["detail"]["errors"][0]["panel_id"] == "2"
    if fault == "slots":
        assert response.headers["Retry-After"] == "1"
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        for model in (ComparisonRun, GenerationRun, ApiUsageLog):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_analysis_comparison_reuses_strict_provider_schema_and_full_transcript(
    data, client, student, analysis_comparison, monkeypatch, provider
):
    from s2_integration_fixtures import workflow_transport
    from s4_analysis_fixtures import prompt_inputs
    from test_provider_connections import KEY
    from test_student_generation import frames

    from src.services.model_capabilities import capabilities
    from src.services.provider_secrets import encrypt_key

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
        "analysis": dict(
            status="succeeded",
            credential_revision=1,
            connection_version=connection.connection_version,
            capability_definition_version=definition,
            role_contract_version="s4-v2",
        )
    }
    options = {
        "openai": dict(max_output_tokens=8192, reasoning=dict(effort="medium")),
        "anthropic": dict(
            max_output_tokens=8192, thinking=dict(type="disabled")
        ),
        "google": dict(max_output_tokens=8192, thinking=dict(budget=0)),
    }[provider]
    await data.db.commit()
    analysis_comparison["panels"][0]["model_config"].update(
        provider=provider, model_id=model.model_id, options=options
    )
    mocked = workflow_transport(
        monkeypatch, provider, budget=8192, analysis_budget=8192
    )
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=analysis_comparison)
    terminal = frames(response)[-1]
    assert (
        terminal[0] == "output.completed"
        and terminal[1]["outcome_status"] == "ok"
    ), response.text
    assert len(mocked.calls) == 1
    assert (
        prompt_inputs(mocked.payloads[0])["messages"]
        == terminal[1]["result"]["messages"]
    )
    assert "message_classifications" in mocked.schemas[0]
    assert all(
        sdk.is_closed if provider == "google" else sdk.is_closed()
        for sdk in mocked.clients
    )

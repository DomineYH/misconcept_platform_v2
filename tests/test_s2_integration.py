"""Authoring to CSV on contracted fresh/restored databases with three SDKs."""

import asyncio
import copy
import csv
import io
import json
from uuid import uuid4

import pytest
from s2_integration_fixtures import (
    authenticate,
    installation,
    workflow_api,
    workflow_transport,
)
from sqlalchemy import select
from test_lesson_snapshots import start
from test_provider_connections import KEY, PASSWORD
from test_s2_cutover import source
from test_scenario_drafts import post
from test_student_generation import frames

from src.models import ApiUsageLog, ModelConfig, ProviderConnection, Session
from src.services.model_capabilities import capabilities
from src.services.model_verification import ROLE_CONTRACT_VERSIONS

__all__ = ["installation", "workflow_api", "source"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_contracted_installation_authoring_to_csv(
    installation, workflow_api, monkeypatch, provider, entry
):
    from analysis_test_helpers import AnalysisApi

    api = AnalysisApi(workflow_api)
    transport = workflow_transport(monkeypatch, provider, analysis_budget=8192)
    await authenticate(api, "admin")
    state = (await api.get("/admin/ai/state")).json()
    connection = next(
        p for p in state["providers"] if p["provider"] == provider
    )
    saved_key = await post(
        api,
        f"/admin/ai/providers/{provider}/key",
        dict(
            current_password=PASSWORD,
            expected_version=connection["connection_version"],
            api_key=KEY,
        ),
    )
    assert saved_key.status_code == 200, saved_key.text
    model_id = dict(
        openai="gpt-5-mini",
        anthropic="claude-sonnet-4-6",
        google="gemini-2.5-flash",
    )[provider]
    registered = await post(
        api,
        "/admin/ai/models",
        dict(
            provider=provider,
            model_id=model_id,
            display_name="Workflow model",
        ),
    )
    assert registered.status_code == 200, registered.text
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
    # Role evidence is a prerequisite fixture; generation still crosses the real SDK.
    async with installation.factory() as db:
        connection = await db.scalar(
            select(ProviderConnection).where(
                ProviderConnection.provider == provider
            )
        )
        model = await db.scalar(
            select(ModelConfig).where(
                ModelConfig.provider_connection_id == connection.id
            )
        )
        definition = capabilities(provider, model_id)["definition_version"]
        model.enabled = True
        model.capability_definition_version = definition
        model.verification_state = {
            role: dict(
                status="succeeded",
                credential_revision=connection.credential_revision,
                connection_version=connection.connection_version,
                capability_definition_version=definition,
                role_contract_version=ROLE_CONTRACT_VERSIONS[role],
            )
            for role in ("student", "mentor", "analysis")
        }
        await db.commit()
        selection = dict(
            model_config_id=model.id,
            provider_connection_id=connection.id,
            provider=provider,
            model_id=model_id,
            options=options,
        )
    assert transport.calls == []
    listing = (await api.get("/admin/scenarios")).text
    assert "시나리오" in listing
    # Both installations have one real seeded/converted draft, at its original ID.
    saved = (await api.get("/admin/scenarios/1")).json()
    assert saved["status"] == "draft"
    assert saved["review_required"] == installation.converted
    body = {
        key: saved[key]
        for key in (
            "title",
            "subject",
            "target_grade",
            "groups",
            "config_schema_version",
            "config",
        )
    }
    body.update(expected_version=saved["config_version"], action="save_draft")
    body["title"] = "Frozen workflow title"
    body["groups"] = [installation.group_id]
    body["config"]["problem"] = dict(
        public_text='Public {x} {{literal}} {"answer":1}',
        learning_objective="Compare fractions",
    )
    body["config"]["student"].update(
        name="Frozen student",
        public_profile="Public introduction",
        internal_profile="PRIVATE_STUDENT",
        misconception="PRIVATE_MISCONCEPTION",
        behavior_instruction='PRIVATE_BEHAVIOR {x} {{literal}} {"answer":1}',
        resolved_model_config=selection,
    )
    body["config"]["mentor"].update(
        mode="auto" if entry == "api" else "manual",
        name="Frozen mentor",
        behavior_instruction="PRIVATE_MENTOR",
        resolved_model_config=selection,
        intervention_policy=dict(
            condition="PRIVATE_CONDITION",
            start_turn=1,
            min_interval_turns=2,
            window_turns=10,
            max_interventions=3,
        ),
    )
    body["config"]["analysis"].update(
        context="PRIVATE_ANALYSIS",
        expected_understanding="PRIVATE_ANSWER",
        instruction="PRIVATE_EVALUATION",
        classification_enabled=entry == "api",
        rubric_name="Frozen rubric",
        rubric=[
            dict(
                id="A",
                name="Frozen label",
                criteria="PRIVATE_CRITERIA",
                level="high",
            )
        ],
        resolved_model_config={
            **selection,
            "options": {**options, "max_output_tokens": 8192},
        },
    )
    repaired = await post(api, "/admin/scenarios/1/update", body)
    assert repaired.status_code == 200, repaired.text
    body.update(
        expected_version=repaired.json()["version"],
        action="publish",
        acknowledge_review=True,
    )
    published = await post(api, "/admin/scenarios/1/update", body)
    assert published.status_code == 200, published.text
    assert not published.json()["review_required"]
    await authenticate(api, "owner")
    response, session_id = await start(api, 1, entry)
    assert response.status_code == (
        201 if entry == "api" else 200
    ), response.text
    async with installation.factory() as db:
        session = await db.get(Session, session_id)
        frozen = (
            session.config_snapshot_json,
            session.config_hash,
            session.source_scenario_version,
        )
        assert session.snapshot_origin == "native"
        assert session.source_scenario_version == published.json()["version"]
    chat = await api.get("/scenarios/1")
    assert (
        "Public introduction" in chat.text
        and "Frozen workflow title" in chat.text
    )
    assert "PRIVATE_" not in chat.text and KEY not in chat.text
    assert (
        await api.post(
            f"/sessions/{session_id}/turns/stream",
            json=dict(request_id=str(uuid4()), content="Why?"),
        )
    ).status_code == 403
    # One winning save, one visible conflict; the active native lesson stays frozen.
    await authenticate(api, "admin")
    edited = copy.deepcopy(body)
    edited.update(
        expected_version=published.json()["version"],
        action="save_draft",
        title="Changed title",
    )
    edited["config"]["student"]["behavior_instruction"] = "CHANGED STUDENT"
    edited["config"]["mentor"]["behavior_instruction"] = "CHANGED MENTOR"
    edited["config"]["analysis"]["instruction"] = "CHANGED ANALYSIS"
    race = await asyncio.gather(
        *(post(api, "/admin/scenarios/1/update", edited) for _ in range(2))
    )
    assert sorted(r.status_code for r in race) == [200, 409]
    async with installation.factory() as db:
        model = await db.get(ModelConfig, selection["model_config_id"])
        model.default_options_json = {"unknown_current_option": True}
        model.config_version += 1
        await db.commit()
    await authenticate(api, "owner")
    turn_request = dict(request_id=str(uuid4()), content="Why {x}?")
    async with installation.factory() as db:
        model = await db.get(ModelConfig, selection["model_config_id"])
        model.enabled = False
        await db.commit()
    blocked = await post(
        api, f"/sessions/{session_id}/turns/stream", turn_request
    )
    assert blocked.status_code == 503, blocked.text
    assert transport.calls == []
    async with installation.factory() as db:
        model = await db.get(ModelConfig, selection["model_config_id"])
        model.enabled = True
        await db.commit()
    result = await post(
        api, f"/sessions/{session_id}/turns/stream", turn_request
    )
    terminal = frames(result)[-1]
    assert terminal[0] == "output.completed", result.text
    assert terminal[1]["message"]["content"] == "Student answer"
    turn_id = terminal[1]["turn_id"]
    before = len(transport.calls)
    replay = await post(
        api, f"/sessions/{session_id}/turns/stream", turn_request
    )
    assert replay.json()["run_id"] == terminal[1]["run_id"]
    assert replay.json()["message"]["content"] == "Student answer"
    assert len(transport.calls) == before
    mentor_path = f"/sessions/{session_id}/turns/{turn_id}/mentor/stream"
    mentor_request = dict(
        request_id=str(uuid4()), trigger="auto" if entry == "api" else "manual"
    )
    coached = await post(api, mentor_path, mentor_request)
    assert (
        frames(coached)[-1][1]["message"]["content"] == "Mentor coaching"
    ), coached.text
    before = len(transport.calls)
    replay = await post(api, mentor_path, mentor_request)
    assert replay.json()["run_id"] == frames(coached)[-1][1]["run_id"]
    assert replay.json()["message"]["content"] == "Mentor coaching"
    assert len(transport.calls) == before
    ended = await post(
        api, f"/sessions/{session_id}/end", {"request_id": str(uuid4())}
    )
    assert ended.status_code == 202
    from test_analysis_runs import terminal

    await terminal(api, ended.json()["actions"]["status"])
    analyzed = await post(api, f"/sessions/{session_id}/analyze", {})
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["distribution"] == (
        {"A": 1} if entry == "api" else {}
    )
    assert "Good question" in analyzed.text
    public = await api.get(f"/sessions/{session_id}/analysis")
    exported = await api.get(f"/sessions/{session_id}/export.csv")
    assert public.status_code == exported.status_code == 200
    for path in ("analysis_page", "analysis_modal", "messages/updates"):
        rendered = await api.get(f"/sessions/{session_id}/{path}")
        assert rendered.status_code == 200
        assert "PRIVATE_" not in rendered.text and KEY not in rendered.text
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert (
        rows
        and "Frozen workflow title" in exported.text
        and "Frozen student" in exported.text
    )
    assert (
        "Frozen label" if entry == "api" else "classification_disabled"
    ) in exported.text
    assert (
        "PRIVATE_" not in exported.text + public.text
        and KEY not in exported.text + public.text
    )
    await authenticate(api, "admin")
    transport.state.fail_analysis = True
    regenerated = await post(
        api, f"/admin/sessions/{session_id}/analyze_regenerate", {}
    )
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["regeneration_status"].endswith("_preserved")
    await authenticate(api, "owner")
    preserved = (await api.get(f"/sessions/{session_id}/analysis")).json()
    assert preserved["accepted_report"] == public.json()["accepted_report"]
    latest = preserved["latest_run"]
    assert (latest["status"], latest["preserved"], latest["error_code"]) == (
        "failed",
        True,
        "invalid_json",
    )
    assert latest["run_id"] != public.json()["latest_run"]["run_id"]
    assert latest["adopted"] is False
    assert {
        k: v
        for k, v in preserved.items()
        if k not in {"latest_run", "regeneration_status"}
    } == {
        k: v
        for k, v in public.json().items()
        if k not in {"latest_run", "regeneration_status"}
    }
    if installation.converted:
        historical = await api.get("/sessions/1/export.csv")
        assert (
            "Original feedback" in historical.text
            and "Original label" in historical.text
        )
        blocked = await post(api, "/sessions/1/analyze", {})
        assert (
            blocked.status_code == 409
            and blocked.json()["detail"]["code"] == "legacy_read_only"
        )
    await authenticate(api, "other")
    assert (
        await api.get(f"/sessions/{session_id}/export.csv")
    ).status_code == 403
    async with installation.factory() as db:
        session = await db.get(Session, session_id)
        assert (
            session.config_snapshot_json,
            session.config_hash,
            session.source_scenario_version,
        ) == frozen
        attempts = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.session_id == session_id)
            )
        ).all()
        assert attempts and all(
            a.provider == provider and a.model == model_id for a in attempts
        )
    # Payloads retain literal text/options and keep private roles out of other inputs.
    student_payload = json.dumps(transport.payloads[0], ensure_ascii=False)
    assert "PRIVATE_BEHAVIOR {x} {{literal}}" in student_payload
    assert (
        "PRIVATE_BEHAVIOR" in student_payload
        and "PRIVATE_MENTOR" not in student_payload
        and "PRIVATE_ANALYSIS" not in student_payload
    )
    for payload, schema in zip(transport.payloads, transport.schemas):
        text = json.dumps(payload, ensure_ascii=False)
        assert "CHANGED " not in text and KEY not in text
        output_cap = 8192 if "message_classifications" in schema else 1024
        if provider == "openai":
            assert payload["max_output_tokens"] == output_cap and payload[
                "reasoning"
            ] == {"effort": "low"}
        elif provider == "anthropic":
            assert (
                payload["max_tokens"] == output_cap
                and payload["thinking"] == {"type": "disabled"}
                and payload["temperature"] == 0.4
            )
        else:
            assert (
                payload["generationConfig"]["maxOutputTokens"] == output_cap
                and payload["generationConfig"]["temperature"] == 0.4
            )
    for payload, schema in zip(transport.payloads, transport.schemas):
        text = json.dumps(payload)
        if "brief_feedback" in schema:
            assert "PRIVATE_EVALUATION" in text and "PRIVATE_MENTOR" not in text
        elif (
            "results" not in schema
            and "label" not in schema
            and payload is not transport.payloads[0]
        ):
            assert "PRIVATE_MENTOR" in text and "PRIVATE_ANALYSIS" not in text
    assert sum("results" in s for s in transport.schemas) == 0
    assert sum("label" in s for s in transport.schemas) == 0
    assert sum("message_classifications" in s for s in transport.schemas) == 2
    assert all(
        (c.is_closed if provider == "google" else c.is_closed())
        for c in transport.clients
    )

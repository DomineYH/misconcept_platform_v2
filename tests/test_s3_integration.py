"""Explicit role probes to isolated student/mentor calls through three SDKs."""

import json
from copy import deepcopy
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
from test_role_probes import (
    CLASSIFICATION,
    MENTOR_NEGATIVE,
    MENTOR_POSITIVE,
    SYNTHESIS,
)
from test_s2_cutover import source
from test_scenario_drafts import post
from test_student_generation import frames
from test_student_probe import completed

from src.models import ApiUsageLog, GenerationRun, ModelConfig, Session
from src.services import context_budget
from src.services.model_capabilities import capabilities

__all__ = ["installation", "workflow_api", "source", "cleanup_probes"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture(autouse=True)
async def cleanup_probes(workflow_api):
    yield
    from src.services.probe_execution import stop_probes

    await stop_probes()


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_explicit_probes_then_student_only_and_single_mentor_events(
    installation, workflow_api, monkeypatch, provider
):
    api = workflow_api
    await authenticate(api, "admin")
    state = (await api.get("/admin/ai/state")).json()
    connection = next(
        p for p in state["providers"] if p["provider"] == provider
    )
    from test_provider_connections import KEY, PASSWORD

    assert (
        await post(
            api,
            f"/admin/ai/providers/{provider}/key",
            dict(
                current_password=PASSWORD,
                expected_version=connection["connection_version"],
                api_key=KEY,
            ),
        )
    ).status_code == 200
    model_id = dict(
        openai="gpt-5-mini",
        anthropic="claude-sonnet-4-6",
        google="gemini-2.5-flash",
    )[provider]
    registered = await post(
        api,
        "/admin/ai/models",
        dict(provider=provider, model_id=model_id, display_name="S3 model"),
    )
    assert registered.status_code == 200, registered.text
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    transports = []
    for role, budget in (
        ("student", 1024),
        ("mentor", 1500),
        ("analysis", 2500),
    ):
        transport = workflow_transport(monkeypatch, provider, budget=budget)
        if role == "mentor":
            transport.state.structured_results = [
                MENTOR_POSITIVE,
                MENTOR_NEGATIVE,
            ]
        elif role == "analysis":
            transport.state.structured_results = [CLASSIFICATION, SYNTHESIS]
        transports.append(transport)
        body = dict(
            expected_version=model["config_version"],
            role=role,
            request_id=str(uuid4()),
        )
        path = f"/admin/ai/models/{model['id']}/probes"
        assert (await post(api, path, body)).status_code == 202
        result = await completed(api, body["request_id"])
        assert result["status"] == "succeeded", result
        assert (await post(api, path, body)).status_code == 202
        assert len(transport.calls) == 2
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert (
        model["verification_state"]["student"]["role_contract_version"]
        == "s1-v1"
    )
    assert (
        model["verification_state"]["mentor"]["role_contract_version"]
        == "s3-v1"
    )
    assert (
        model["verification_state"]["analysis"]["role_contract_version"]
        == "s1-v1"
    )
    assert (
        await post(
            api,
            f"/admin/ai/models/{model['id']}/update",
            dict(
                expected_version=model["config_version"],
                display_name="S3 model",
                enabled=True,
                default_options={},
            ),
        )
    ).status_code == 200

    saved = (await api.get("/admin/scenarios/1")).json()
    body = {
        k: saved[k]
        for k in (
            "title",
            "subject",
            "target_grade",
            "groups",
            "config_schema_version",
            "config",
        )
    }
    body.update(
        expected_version=saved["config_version"],
        action="publish",
        acknowledge_review=True,
    )
    body["groups"] = [installation.group_id]
    async with installation.factory() as db:
        stored_model = await db.get(ModelConfig, model["id"])
        connection_id = stored_model.provider_connection_id
    selection = dict(
        model_config_id=model["id"],
        provider_connection_id=connection_id,
        provider=provider,
        model_id=model_id,
        options={"max_output_tokens": 1024},
    )
    body["config"]["student"].update(
        name="Frozen student",
        misconception="Add fraction denominators",
        behavior_instruction="반말로 말하고 교사에게 되물어라. {literal}",
        resolved_model_config=selection,
    )
    body["config"]["mentor"].update(
        mode="auto",
        name="Frozen mentor",
        behavior_instruction="Coach the teacher",
        resolved_model_config=selection,
    )
    body["config"]["mentor"]["intervention_policy"].update(
        condition="Coach when useful", start_turn=2, min_interval_turns=2
    )
    body["config"]["problem"]["learning_objective"] = "Compare fractions"
    body["config"]["analysis"].update(
        context="PRIVATE ANALYSIS",
        expected_understanding="PRIVATE ANSWER",
        instruction="PRIVATE EVALUATION",
        classification_enabled=False,
        rubric=[],
        resolved_model_config=selection,
    )
    published = await post(api, "/admin/scenarios/1/update", body)
    assert published.status_code == 200, published.text
    await authenticate(api, "owner")
    started, session_id = await start(api, 1, "api")
    assert started.status_code == 201, started.text
    transport = workflow_transport(monkeypatch, provider)
    transport.state.student_answer = "분모도 더하면 되는 거 아냐? 왜 아니야?"
    transports.append(transport)
    student_path = f"/sessions/{session_id}/turns/stream"
    first_body = dict(request_id=str(uuid4()), content="First target")
    first = frames(await post(api, student_path, first_body))[-1][1]
    assert (
        first["message"]["content"] == "분모도 더하면 되는 거 아냐? 왜 아니야?"
    )
    assert len(transport.calls) == 1 and transport.schemas == [{}]
    assert "반말로 말하고 교사에게 되물어라. {literal}" in json.dumps(
        transport.payloads[0], ensure_ascii=False
    )
    replay = await post(api, student_path, first_body)
    assert replay.json()["run_id"] == first["run_id"]
    mentor_path = (
        f"/sessions/{session_id}/turns/{first['turn_id']}/mentor/stream"
    )
    refused = await post(
        api, mentor_path, dict(request_id=str(uuid4()), trigger="auto")
    )
    assert (
        refused.status_code == 409
        and refused.json()["detail"]["code"] == "mentor_start_turn"
    )
    assert len(transport.calls) == 1
    definition = capabilities(provider, model_id)
    definition.update(combined_context_tokens=1025, input_token_limit=1)
    with monkeypatch.context() as small:
        small.setattr(context_budget, "capabilities", lambda *_: definition)
        rejected = await post(
            api, mentor_path, dict(request_id=str(uuid4()), trigger="manual")
        )
        assert (
            rejected.status_code == 422
            and rejected.json()["detail"]["code"] == "context_limit"
        )
    assert len(transport.calls) == 1
    mentor_body = dict(request_id=str(uuid4()), trigger="manual")
    coached = frames(await post(api, mentor_path, mentor_body))[-1][1]
    assert coached["message"]["content"] == "Mentor coaching"
    assert len(transport.calls) == 2
    for request_body in (
        mentor_body,
        dict(request_id=str(uuid4()), trigger="manual"),
    ):
        duplicate = await post(api, mentor_path, request_body)
        assert duplicate.json()["run_id"] == coached["run_id"]
    assert len(transport.calls) == 2
    second = frames(
        await post(
            api,
            student_path,
            dict(request_id=str(uuid4()), content="Second target"),
        )
    )[-1][1]
    transport.state.structured_results = [
        dict(
            should_intervene=False,
            feedback="",
            reason_summary="Authored condition",
        )
    ]
    negative_path = (
        f"/sessions/{session_id}/turns/{second['turn_id']}/mentor/stream"
    )
    negative_body = dict(request_id=str(uuid4()), trigger="auto")
    negative = frames(await post(api, negative_path, negative_body))[-1][1]
    assert (
        negative["result_kind"] == "no_intervention"
        and negative["message"] is None
    )
    assert (await post(api, negative_path, negative_body)).json()[
        "run_id"
    ] == negative["run_id"]
    assert len(transport.calls) == 4

    async with installation.factory() as db:
        session = await db.get(Session, session_id)
        frozen = deepcopy(session.config_snapshot_json), session.config_hash
        assert (
            session.tutor_question_count == 2
            and session.tutor_intervention_count == 1
        )
        runs = (
            await db.scalars(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id
                )
            )
        ).all()
        assert len(runs) == 4
        assert [
            r.mentor_reason_summary for r in runs if r.operation == "mentor"
        ] == ["Authored condition"] * 2
        attempts = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
        assert [
            (a.operation, a.probe_step)
            for a in attempts
            if a.operation == "probe"
        ] == [
            ("probe", "text"),
            ("probe", "stream"),
            ("probe", "manual_positive"),
            ("probe", "auto_negative"),
            ("probe", "classification"),
            ("probe", "synthesis"),
        ]
        lesson_attempts = [a for a in attempts if a.session_id == session_id]
        assert [a.operation for a in lesson_attempts] == [
            "student",
            "mentor",
            "student",
            "mentor",
        ]
        assert all(
            a.status == "completed"
            and a.attempt_no == 1
            and a.context_budget_json
            for a in lesson_attempts
        )
        assert all(
            a.context_budget_json is None
            for a in attempts
            if a.operation == "probe"
        )
    for path in (
        f"/runs/{coached['run_id']}",
        f"/sessions/{session_id}/export.csv",
        f"/sessions/{session_id}/messages/updates",
    ):
        response = await api.get(path)
        assert response.status_code == 200
        assert "Authored condition" not in response.text
        assert (
            "context_budget_json" not in response.text
            and "estimated_input_tokens" not in response.text
        )

    # A separate off lesson proves direct endpoint attacks use no mentor attempt.
    await authenticate(api, "admin")
    body["config"]["mentor"]["mode"] = "off"
    body["expected_version"] = published.json()["version"]
    assert (
        await post(api, "/admin/scenarios/1/update", body)
    ).status_code == 200
    await authenticate(api, "owner")
    _, off_id = await start(api, 1, "api")
    off_turn = frames(
        await post(
            api,
            f"/sessions/{off_id}/turns/stream",
            dict(request_id=str(uuid4()), content="Off target"),
        )
    )[-1][1]
    for trigger in ("manual", "auto"):
        off = await post(
            api,
            f"/sessions/{off_id}/turns/{off_turn['turn_id']}/mentor/stream",
            dict(request_id=str(uuid4()), trigger=trigger),
        )
        assert (
            off.status_code == 400
            and off.json()["detail"]["code"] == "mentor_disabled"
        )
    assert len(transport.calls) == 5
    async with installation.factory() as db:
        session = await db.get(Session, session_id)
        assert (session.config_snapshot_json, session.config_hash) == frozen
        off_attempts = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.session_id == off_id)
            )
        ).all()
        assert [a.operation for a in off_attempts] == ["student"]
    assert all(
        c.is_closed if provider == "google" else c.is_closed()
        for t in transports
        for c in t.clients
    )

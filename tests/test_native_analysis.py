"""Frozen post-session analysis at the authenticated HTTP/provider boundary."""

import copy
import json
from datetime import datetime, timezone

import httpx2
import pytest
from lesson_fixtures import install_connection, install_snapshot
from test_analysis_invocations import USAGE, analysis_transport, result_for
from test_analysis_invocations import api as analysis_api
from test_provider_connections import api as provider_api
from test_student_probe import response_body

from src.models import Message
from src.services.lesson_snapshots import canonical_hash

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def native_analysis(data, monkeypatch, *, enabled=True):
    connection, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": dict(model.verification_state["student"]),
    }
    await install_snapshot(data, connection, model, context_turn_limit=1)
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"].update(
        classification_enabled=enabled,
        rubric_name="Frozen rubric",
        rubric_description="Frozen description",
        category_name="Frozen category",
        rubric=[
            dict(
                id="A",
                name="Explore",
                criteria="PRIVATE CRITERIA",
                level="high",
            ),
            dict(id="B", name="Tell", criteria="PRIVATE LOW", level="low"),
            dict(id="C", name="Other", criteria="PRIVATE NEUTRAL", level=None),
        ],
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    data.session.ended_at = datetime.now(timezone.utc)
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role="teacher",
                content="Early question {x}",
            ),
            Message(
                session_id=data.session.id,
                role="student",
                content="Early answer",
            ),
            Message(session_id=data.session.id, role="teacher", content="Why?"),
        ]
    )
    await data.db.commit()
    return connection, model


async def test_analysis_uses_frozen_inputs_and_one_model_option_set(
    data, api, monkeypatch
):
    _, model = await native_analysis(data, monkeypatch)
    data.scenario.title = "Changed title"
    data.scenario.prompt = "Changed misconception"
    data.framework.labels = ["Changed", "Labels"]
    model.default_options_json = {
        "max_output_tokens": 7,
        "reasoning": {"effort": "low"},
    }
    data.db.add(
        Message(
            session_id=data.session.id,
            role="tutor",
            content="Late mentor coaching",
        )
    )
    await data.db.commit()

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    assert response.json()["distribution"] == {"A": 2, "B": 0, "C": 0}
    assert len(calls) == 4 and all(c.is_closed() for c in clients)
    assert all(
        c["max_output_tokens"] == 1500
        and c["reasoning"] == {"effort": "medium"}
        for c in calls
    )
    for body in calls[1:]:
        prompt = body["input"][0]["content"]
        assert "Late mentor coaching" not in prompt
        assert all(
            value in prompt
            for value in [
                "PRIVATE ANALYSIS",
                "PRIVATE ANSWER",
                "PRIVATE EVALUATION",
                "PRIVATE CRITERIA",
                "Early question {x}",
            ]
        )
        assert (
            "Changed title" not in prompt
            and "Changed misconception" not in prompt
        )


async def test_classification_off_keeps_feedback_and_explicit_display(
    data, api, monkeypatch
):
    await native_analysis(data, monkeypatch, enabled=False)

    from sqlalchemy import select

    teacher_id = await data.db.scalar(
        select(Message.id).where(Message.role == "teacher").order_by(Message.id)
    )
    student_id = await data.db.scalar(
        select(Message.id).where(Message.role == "student")
    )
    feedback = {
        "brief_feedback": ["Good question"],
        "strengths": [
            {
                "message_id": teacher_id,
                "quote": "Early question {x}",
                "reason": "Narrative strength",
            }
        ],
        "improvements": [
            {
                "student_message_id": student_id,
                "student_quote": "Early answer",
                "missed_reason": "Narrative improvement",
                "alternative_question": "What about halves?",
                "alternative_reason": "Compare parts",
            }
        ],
        "dialogue_coaching": [],
    }

    async def upstream(request, body):
        assert body["text"]["format"]["name"] == "RuntimeSynthesis"
        return httpx2.Response(
            200, json=response_body(json.dumps(feedback), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert (
        response.status_code == 200
        and response.json()["feedback"] == "Good question"
    )
    assert len(calls) == 1 and clients[0].is_closed()
    report = await api.get(f"/sessions/{data.session.id}/analysis")
    assert report.json()["classification_enabled"] is False
    assert report.json()["label_names"] == {}
    page = await api.get(f"/sessions/{data.session.id}/analysis_page")
    assert "분류 미사용" in page.text and "PRIVATE CRITERIA" not in page.text
    assert (
        "Narrative strength" in page.text
        and "Narrative improvement" in page.text
        and "What about halves?" in page.text
    )
    csv = await api.get(f"/sessions/{data.session.id}/export.csv")
    assert "classification_disabled" in csv.text


@pytest.mark.parametrize(
    "label,grade,level",
    [
        ("A", "우수", "high"),
        ("B", "개선", "low"),
        ("C", None, None),
        ("unknown", None, None),
    ],
)
async def test_rubric_ids_control_storage_display_and_levels(
    data, api, monkeypatch, label, grade, level
):
    await native_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        if body["text"]["format"]["name"] == "RuntimeClassification":
            value["label"] = label
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200 and len(calls) == 4
    report = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    question = report["questions"][0]
    assert (
        question["grade"] == grade and report["messages"][0]["level"] == level
    )
    if label == "unknown":
        assert question["label"] == "Unclassified" and report[
            "distribution"
        ] == {"A": 0, "B": 0, "C": 0}
        from sqlalchemy import select

        from src.models import ApiUsageLog, QuestionAnalysis

        async with data.factory() as db:
            assert (await db.scalars(select(QuestionAnalysis))).all() == []
            attempts = (
                await db.scalars(
                    select(ApiUsageLog).where(
                        ApiUsageLog.operation == "classification"
                    )
                )
            ).all()
            assert all(
                a.status == "failed" and a.error_code == "invalid_output"
                for a in attempts
            )
    else:
        name = {"A": "Explore", "B": "Tell", "C": "Other"}[label]
        assert question["label"] == label and question["label_name"] == name
        page = await api.get(f"/sessions/{data.session.id}/analysis_page")
        assert name in page.text and "PRIVATE CRITERIA" not in page.text
        csv = await api.get(f"/sessions/{data.session.id}/export.csv")
        assert name in csv.text and "PRIVATE CRITERIA" not in csv.text


@pytest.mark.parametrize(
    "blocked",
    ["group", "inactive", "deleted", "connection", "role", "legacy", "corrupt"],
)
async def test_analysis_checks_current_authority_and_native_provenance(
    data, api, monkeypatch, blocked
):
    connection, model = await native_analysis(data, monkeypatch)
    if blocked == "group":
        data.owner.group_id = None
    elif blocked == "inactive":
        data.scenario.is_active = False
    elif blocked == "deleted":
        data.scenario.mark_deleted()
    elif blocked == "connection":
        connection.enabled = False
    elif blocked == "role":
        model.verification_state = {"analysis": {"status": "unverified"}}
    elif blocked == "legacy":
        data.session.snapshot_origin = "legacy_reconstructed"
    else:
        data.session.config_hash = "invalid"
    await data.db.commit()

    async def upstream(request, body):
        raise AssertionError("Blocked lesson must not execute")

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code in (400, 403, 404) and calls == clients == []


@pytest.mark.parametrize(
    "failure", ["synthesis", "unknown_id", "greeting", "role"]
)
async def test_reanalysis_keeps_frozen_inputs_and_preserves_good_result_on_failure(
    data, api, monkeypatch, failure
):
    from sqlalchemy import select
    from test_scenario_api import login

    from src.config import config

    _, model = await native_analysis(data, monkeypatch)
    teacher_id = await data.db.scalar(
        select(Message.id).where(Message.role == "teacher").order_by(Message.id)
    )
    mode = "valid"

    async def upstream(request, body):
        value = result_for(body)
        name = body["text"]["format"]["name"]
        if name == "RuntimeSynthesis":
            value["strengths"] = [
                {
                    "message_id": teacher_id,
                    "quote": "Early question {x}",
                    "reason": "Original strength",
                }
            ]
            if mode == "synthesis":
                return httpx2.Response(
                    200, json=response_body("not json", USAGE)
                )
        if name == "RuntimeGreetings" and mode == "greeting":
            return httpx2.Response(200, json=response_body("not json", USAGE))
        if name == "RuntimeClassification" and mode == "unknown_id":
            value["label"] = "arbitrary"
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    first = await api.post(
        f"/sessions/{data.session.id}/analyze", headers=headers
    )
    assert first.json()["feedback_status"] == "ok"
    original = copy.deepcopy(calls)
    data.scenario.config_json = {"changed": "current settings must not execute"}
    data.scenario.title = "Changed title"
    data.scenario.status = "draft"
    data.framework.labels = ["Changed", "Labels"]
    model.default_options_json = {"unsupported_current_option": True}
    model.config_version += 1
    monkeypatch.setattr(config, "ANALYSIS_MODEL", "unregistered-env-model")
    monkeypatch.setattr(config, "ANALYSIS_REASONING", "unsupported-env-effort")
    await data.db.commit()
    login(api, data.admin)
    await api.get("/health")
    path = f"/admin/sessions/{data.session.id}/analyze_regenerate"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    rerun = await api.post(path, headers=headers)
    assert (
        rerun.status_code == 200
        and rerun.json()["regeneration_status"] == "replaced"
    )
    assert sorted(
        json.dumps(c, sort_keys=True) for c in calls[len(original) :]
    ) == sorted(json.dumps(c, sort_keys=True) for c in original)
    assert rerun.json()["questions"][0]["label_name"] == "Explore"
    mode = failure
    if failure == "role":
        model.verification_state = {"analysis": {"status": "unverified"}}
        await data.db.commit()
    before = len(calls)
    failed = await api.post(path, headers=headers)
    if failure == "role":
        assert failed.status_code == 400 and len(calls) == before
    else:
        assert failed.status_code == 200
        assert failed.json()["regeneration_status"].endswith("_preserved")
        assert failed.json()["feedback_status"] == "ok"
    login(api, data.owner)
    preserved = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    assert preserved["distribution"] == {"A": 2, "B": 0, "C": 0}
    assert (
        preserved["feedback_sections"]["strengths"][0]["reason"]
        == "Original strength"
    )


async def test_analysis_retry_rechecks_current_group_before_external_attempt(
    data, api, monkeypatch
):
    from sqlalchemy import select

    from src.models import ApiUsageLog, User

    await native_analysis(data, monkeypatch, enabled=False)

    async def upstream(request, body):
        async with data.factory() as db:
            owner = await db.get(User, data.owner.id)
            owner.group_id = None
            await db.commit()
        return httpx2.Response(
            503,
            json={"error": {"code": "server_error"}},
            headers={"Retry-After": "0.01"},
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    result = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert (
        result.status_code == 200
        and result.json()["feedback_status"] == "failed"
    )
    assert len(calls) == 1
    async with data.factory() as db:
        attempts = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert [(a.attempt_no, a.error_code) for a in attempts] == [
        (1, "transient"),
        (2, "configuration_unavailable"),
    ]


async def test_admin_analysis_and_csv_map_frozen_names(data, api, monkeypatch):
    from test_scenario_api import login

    await native_analysis(data, monkeypatch)

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    analysis_transport(monkeypatch, upstream)
    await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    login(api, data.admin)
    page = await api.get("/admin/analysis-page")
    assert page.status_code == 200
    assert 'value="A"' in page.text
    assert page.text.count("Explore") >= 4
    assert "PRIVATE CRITERIA" not in page.text
    export = await api.get("/admin/sessions/export")
    assert (
        export.status_code == 200
        and "Explore" in export.text
        and "enabled" in export.text
    )

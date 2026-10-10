"""Read-only history and reconstruction through HTTP and offline conversion."""

import json
from uuid import uuid4

import pytest
from test_scenario_api import login
from test_scenario_conversion import effective, legacy
from test_scenario_drafts import post
from test_scenario_publication import api, draft, publishable

from src.models import Session
from src.services.scenario_conversion import (
    apply_manifest,
    capture_archive,
    conversion_manifest,
)

__all__ = ["api", "draft", "publishable", "effective", "legacy"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_conversion_records_candidate_provenance_without_claiming_history(
    data, api, legacy, effective
):
    async with data.factory() as db:
        original = await db.get(Session, data.session.id)
        times = original.started_at, original.ended_at
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/source"
        )
        assert original.config_snapshot_json is None
        await apply_manifest(db, archive, manifest)
        await db.commit()
        await db.refresh(original)
        assert original.snapshot_origin == "legacy_reconstructed"
        assert original.snapshot_created_at is not None
        assert original.source_scenario_version is None
        assert (original.started_at, original.ended_at) == times
        envelope = original.config_snapshot_json
        assert envelope["scenario_context"]["title"] == "Test"
        assert envelope["config"]["student"]["name"] == "민수"
        assert (
            "config.student.resolved_model_config" in envelope["unknown_fields"]
        )
        assert (
            "config.student.behavior_instruction" in envelope["unknown_fields"]
        )
        assert "config.analysis.rubric" in envelope["unknown_fields"]
        assert "PRIVATE video" not in json.dumps(envelope)
        saved = (envelope, original.config_hash, original.snapshot_created_at)
        await apply_manifest(db, archive, manifest)
        await db.commit()
        await db.refresh(original)
        assert (
            original.config_snapshot_json,
            original.config_hash,
            original.snapshot_created_at,
        ) == saved

    login(api, data.owner)
    history = await api.get(f"/sessions/{data.session.id}")
    assert history.status_code == 200
    assert "읽기 전용" in history.text
    assert "실제 시작 시점" in history.text
    assert "PRIVATE profile" not in history.text
    assert "PRIVATE misconception" not in history.text
    assert "Evidence" not in history.text


@pytest.mark.parametrize("reconstructed", [False, True])
@pytest.mark.parametrize(
    "operation",
    [
        "turns/stream",
        "mentor",
        "messages",
        "analyze",
        "close",
        "end",
        "admin_regenerate",
        "admin_end",
    ],
)
async def test_legacy_writes_refused_before_touching_original_records(
    data, api, legacy, effective, reconstructed, operation
):
    from sqlalchemy import text

    from src.models import SessionSummary

    async with data.factory() as db:
        db.add(
            SessionSummary(
                session_id=data.session.id,
                distribution_json='{"Original":1}',
                feedback="Original feedback",
            )
        )
        await db.commit()
        if reconstructed:
            archive = await capture_archive(db)
            manifest = await conversion_manifest(
                db, archive, effective, "private/source"
            )
            await apply_manifest(db, archive, manifest)
            await db.commit()
        tables = (
            "session",
            "message",
            "session_summary",
            "question_analysis",
            "api_usage_log",
            "generation_run",
        )
        before = {
            table: (await db.execute(text(f"SELECT * FROM {table}"))).all()
            for table in tables
        }
    login(api, data.admin if operation.startswith("admin_") else data.owner)
    await api.get(f"/sessions/{data.session.id}")
    if operation.startswith("admin_"):
        path = f"/admin/sessions/{data.session.id}/" + (
            "analyze_regenerate" if operation == "admin_regenerate" else "end"
        )
    elif operation == "mentor":
        path = f"/sessions/{data.session.id}/turns/{uuid4()}/mentor/stream"
    else:
        path = f"/sessions/{data.session.id}/{operation}"
    response = await api.post(
        path,
        json=dict(request_id=str(uuid4()), content="Why?", trigger="manual"),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "legacy_read_only"
    async with data.factory() as db:
        assert before == {
            table: (await db.execute(text(f"SELECT * FROM {table}"))).all()
            for table in tables
        }


async def test_mixed_history_and_csv_preserve_originals_and_freeze_display(
    data, api, legacy, effective, publishable
):
    import csv
    import hashlib
    import io
    from datetime import datetime

    from legacy_models import Scenario
    from sqlalchemy import select

    from src.models import (
        ApiUsageLog,
        Message,
        QuestionAnalysis,
        SessionFeedbackReport,
        SessionSummary,
    )

    async with data.factory() as db:
        question = Message(
            session_id=data.session.id,
            role="teacher",
            content="Original question {x} <script>bad()</script>",
            created_at=datetime(2026, 1, 1, 0, 1),
        )
        answer = Message(
            session_id=data.session.id,
            role="student",
            content="Original answer",
            created_at=datetime(2026, 1, 1, 0, 2),
        )
        db.add_all([question, answer])
        await db.flush()
        db.add_all(
            [
                QuestionAnalysis(
                    message_id=question.id,
                    label="Ambiguous original",
                    grade="우수",
                    confidence=0.9,
                    meta_json='{"summary":"Original evidence"}',
                ),
                SessionSummary(
                    session_id=data.session.id,
                    distribution_json='{"Ambiguous original":1}',
                    feedback="Original feedback",
                ),
                SessionFeedbackReport(
                    session_id=data.session.id,
                    version=1,
                    model="Original model",
                    prompt_hash="a" * 64,
                    status="ok",
                    payload_json='{"brief_feedback":"Original feedback"}',
                ),
                ApiUsageLog(
                    session_id=data.session.id,
                    model="Original model",
                    operation="student",
                    input_tokens=7,
                    output_tokens=9,
                ),
            ]
        )
        await db.commit()
        historical = [
            Message,
            QuestionAnalysis,
            SessionSummary,
            SessionFeedbackReport,
            ApiUsageLog,
        ]

        def row_hash(rows):
            return hashlib.sha256(repr(rows).encode()).hexdigest()

        before = {
            model: row_hash(
                (await db.execute(select(*model.__table__.columns))).all()
            )
            for model in historical
        }
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/source"
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
        assert before == {
            model: row_hash(
                (await db.execute(select(*model.__table__.columns))).all()
            )
            for model in historical
        }

    login(api, data.admin)
    await api.get("/admin/ai")
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    login(api, data.owner)
    await api.get("/scenarios")
    native_id = (await post(api, "/sessions", dict(scenario_id=sid))).json()[
        "id"
    ]
    async with data.factory() as db:
        native = await db.get(Session, native_id)
        native.ended_at = datetime(2026, 10, 9)
        db.add(
            Message(
                session_id=native_id, role="student", content="Native answer"
            )
        )
        for scenario_id in (data.scenario.id, sid):
            scenario = await db.get(Scenario, scenario_id)
            scenario.title = "CURRENT TITLE MUST NOT APPEAR"
            scenario.student_name = "CURRENT NAME MUST NOT APPEAR"
        framework = await db.get(type(data.framework), data.framework.id)
        framework.labels_json = '[{"name":"Ambiguous original","criteria":"CURRENT PRIVATE CRITERIA","level":"low"}, "Another"]'
        await db.commit()

    response = await api.get(f"/sessions/{data.session.id}/analysis")
    assert response.status_code == 200
    result = response.json()
    assert result["scenario_title"] == "Test"
    assert result["student_name"] == "민수"
    assert (
        result["snapshot_provenance"]["snapshot_origin"]
        == "legacy_reconstructed"
    )
    assert result["retryable"] is False
    assert result["distribution"] == {"Ambiguous original": 1}
    assert result["questions"][0]["label_name"] == "Ambiguous original"
    assert result["questions"][0]["grade"] == "우수"
    assert result["questions"][0]["reasoning"] == {
        "summary": "Original evidence",
        "improved_sentence": None,
    }
    assert result["framework_label_criteria"] == {}
    for path in (
        f"/sessions/{data.session.id}",
        f"/sessions/{data.session.id}/analysis_page",
        f"/sessions/{data.session.id}/analysis_modal",
    ):
        html = await api.get(path)
        assert html.status_code == 200
        assert "읽기 전용" in html.text
        assert "CURRENT" not in html.text
        assert "PRIVATE profile" not in html.text
        assert "<script>bad()</script>" not in html.text
    csv_response = await api.get(f"/sessions/{data.session.id}/export.csv")
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.text.lstrip("\ufeff"))))
    assert rows[0]["scenario_title"] == "Test"
    assert rows[0]["student_name"] == "민수"
    assert rows[0]["label"] == "Ambiguous original"
    assert rows[0]["snapshot_origin"] == "legacy_reconstructed"
    assert rows[0]["source_scenario_version"] == ""
    assert rows[0]["config_hash_kind"] == "reconstruction_only"
    assert (
        "CURRENT" not in csv_response.text
        and "PRIVATE" not in csv_response.text
    )
    login(api, data.other)
    for path in (
        f"/sessions/{data.session.id}",
        f"/sessions/{data.session.id}/analysis",
        f"/sessions/{data.session.id}/export.csv",
    ):
        assert (await api.get(path)).status_code == 403
    login(api, data.admin)
    for path in (
        "/admin/sessions",
        "/admin/sessions-page",
        f"/admin/sessions/{data.session.id}/detail",
        f"/admin/sessions/{data.session.id}/analysis_modal",
        "/admin/analysis-page",
        "/admin/analysis/1/detail",
        f"/admin/user-conversations/{data.owner.id}/sessions",
    ):
        response = await api.get(path)
        assert response.status_code == 200, response.text
        display_text = (
            response.text.split("<!-- Analysis Table -->")[-1]
            if path == "/admin/analysis-page"
            else response.text
        )
        assert "CURRENT" not in display_text
        assert (
            "legacy_reconstructed" in response.text
            or "읽기 전용" in response.text
        )
    for path in (
        "/admin/sessions/export",
        f"/admin/sessions/{data.session.id}/download",
        f"/admin/user-conversations/{data.owner.id}/export",
    ):
        exported = await api.get(path)
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        old = next(
            row for row in rows if row["session_id"] == str(data.session.id)
        )
        assert old["snapshot_origin"] == "legacy_reconstructed"
        assert old["scenario_title"] == "Test"
        assert "CURRENT" not in exported.text and "PRIVATE" not in exported.text
        if path.endswith("export"):
            new = next(
                row for row in rows if row["session_id"] == str(native_id)
            )
            assert new["snapshot_origin"] == "native"
            assert new["scenario_title"] == publishable["title"]
            assert (
                new["student_name"] == publishable["config"]["student"]["name"]
            )


async def test_new_practice_never_resumes_active_legacy_or_overwrites_native(
    data, api, legacy, effective, publishable
):
    from legacy_models import Scenario
    from sqlalchemy import select

    from src.services.lesson_snapshots import LessonSnapshot, canonical_hash

    async with data.factory() as db:
        history = await db.get(Session, data.session.id)
        history.ended_at = None
        # An existing native snapshot on this legacy scenario is never reconstructed.
        envelope = LessonSnapshot(
            schema_version=1,
            scenario_context=dict(
                title="Original native title", subject="", target_grade=""
            ),
            config=publishable["config"],
        ).model_dump()
        native = Session(
            scenario_id=data.scenario.id,
            teacher_id=data.owner.id,
            config_snapshot_json=envelope,
            config_hash=canonical_hash(envelope),
            source_scenario_version=1,
            snapshot_origin="native",
            snapshot_created_at=history.started_at,
            ended_at=history.started_at,
        )
        db.add(native)
        await db.commit()
        native_id = native.id
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/source"
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
        await db.refresh(native)
        await db.refresh(history)
        assert native.config_snapshot_json == envelope
        assert (
            native.snapshot_origin == "native"
            and native.source_scenario_version == 1
        )
        scenario = await db.get(Scenario, data.scenario.id)
        scenario.config_json = publishable["config"]
        scenario.status = "published"
        scenario.review_required = False
        await db.commit()
        preserved = history.config_snapshot_json
    login(api, data.owner)
    opened = await api.get(f"/scenarios/{data.scenario.id}")
    assert opened.status_code == 200
    reopened = await api.get(f"/scenarios/{data.scenario.id}")
    assert reopened.status_code == 200
    async with data.factory() as db:
        sessions = (
            await db.scalars(select(Session).order_by(Session.id))
        ).all()
        assert len(sessions) == 3
        assert (
            sessions[0].id == data.session.id and sessions[0].ended_at is None
        )
        assert sessions[0].config_snapshot_json == preserved
        assert (
            sessions[1].id == native_id
            and sessions[1].config_snapshot_json == envelope
        )
        assert sessions[2].snapshot_origin == "native"


@pytest.mark.parametrize("damage", ["hash", "revision", "unknown_fields"])
async def test_damaged_reconstruction_has_no_current_display_fallback(
    data, api, legacy, effective, damage
):
    from src.services.lesson_snapshots import canonical_hash

    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/source"
        )
        await apply_manifest(db, archive, manifest)
        session = await db.get(Session, data.session.id, populate_existing=True)
        if damage == "hash":
            session.config_hash = "0" * 64
        elif damage == "revision":
            session.source_scenario_version = 1
        else:
            envelope = dict(session.config_snapshot_json, unknown_fields=[])
            session.config_snapshot_json = envelope
            session.config_hash = canonical_hash(envelope)
        await db.commit()
    login(api, data.owner)
    for path in (
        f"/sessions/{data.session.id}",
        f"/sessions/{data.session.id}/export.csv",
    ):
        response = await api.get(path)
        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "configuration_unavailable"
        }

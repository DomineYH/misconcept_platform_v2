"""Private conversion artifacts and administrator review on temporary SQLite."""

import json

import pytest
from test_scenario_api import login
from test_scenario_drafts import post
from test_scenario_publication import api as provider_api
from test_scenario_publication import draft as draft_fixture
from test_scenario_publication import publishable as publishable_fixture

from src.models import PromptTemplate
from src.services.scenario_conversion import (
    apply_manifest,
    capture_archive,
    conversion_manifest,
)

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
api = provider_api
draft = draft_fixture
publishable = publishable_fixture


@pytest.fixture
def effective():
    operation = dict(
        model_id="gpt-5-mini",
        options={"max_output_tokens": 900, "reasoning": {"effort": "medium"}},
    )
    return dict(
        source="Deployment settings captured by administrator",
        captured_at="2026-10-09T00:00:00Z",
        student_base_rules="BASE: 존댓말, 되묻기 금지",
        student=operation,
        mentor_coaching=operation,
        mentor_judgment=dict(
            model_id="gpt-5.2", options={"max_output_tokens": 200}
        ),
        analysis_synthesis=operation,
        analysis_greeting=dict(
            model_id="gpt-5-mini", options={"max_output_tokens": 500}
        ),
        analysis_classification=dict(
            model_id="gpt-5-mini", options={"max_output_tokens": 2500}
        ),
        tutor_intervention_threshold=3,
        context_turn_limit=10,
    )


@pytest.fixture
async def legacy(data, api, publishable):
    async with data.factory() as db:
        from src.models import Scenario

        scenario = await db.get(Scenario, data.scenario.id)
        student = PromptTemplate(
            bot_type="student",
            template_name="Original student",
            template_text="{scenario_title}: {student_profile}; {prompt}; {{literal}}",
        )
        mentor = PromptTemplate(
            bot_type="tutor",
            template_name="Original mentor",
            template_text="Coach {scenario_title}: {prompt}",
        )
        db.add_all([student, mentor])
        await db.flush()
        scenario.student_template_id = student.id
        scenario.tutor_template_id = mentor.id
        scenario.student_name = "민수"
        scenario.student_profile = "PRIVATE profile {prompt}"
        scenario.prompt = "PRIVATE misconception"
        scenario.problem_situation = "Public fractions"
        scenario.greeting_message = "Welcome"
        scenario.chat_temperature = 0.8
        scenario.tutor_intervention_threshold = 2
        scenario.video_url = "PRIVATE video"
        scenario.video_transcript = "PRIVATE transcript"
        framework = await db.get(type(data.framework), data.framework.id)
        framework.description = "Rubric description"
        framework.category_name = "Question type"
        framework.labels_json = json.dumps(
            [dict(name="Explain", criteria="Evidence", level="high"), "Recall"]
        )
        await db.commit()


async def test_private_dry_run_and_conversion_preserve_reviewable_source(
    data, api, legacy, effective
):
    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        assert manifest == await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        before = await api.get(f"/admin/scenarios/{data.scenario.id}")
        assert before.status_code == 422, "dry run never writes a native config"
        assert (await apply_manifest(db, archive, manifest))[0][
            "status"
        ] == "converted"
        await db.commit()
    saved = (await api.get(f"/admin/scenarios/{data.scenario.id}")).json()
    assert saved["id"] == data.scenario.id
    assert saved["groups"] == [data.owner.group_id]
    assert saved["status"] == "draft" and saved["review_required"]
    config = saved["config"]
    assert config["student"]["public_profile"] == ""
    assert config["student"]["internal_profile"] == "PRIVATE profile {prompt}"
    assert config["student"]["behavior_instruction"] == (
        "BASE: 존댓말, 되묻기 금지\n\nTest: PRIVATE profile {prompt}; PRIVATE misconception; {literal}"
    )
    assert config["mentor"]["mode"] == "auto"
    assert config["mentor"]["intervention_policy"]["max_interventions"] == 2
    assert config["mentor"]["intervention_policy"]["condition"] == ""
    assert config["analysis"]["rubric"] == [
        dict(
            id="legacy_001", name="Explain", criteria="Evidence", level="high"
        ),
        dict(id="legacy_002", name="Recall", criteria="", level=None),
    ]
    assert config["analysis"]["rubric_description"] == "Rubric description"
    assert (
        config["student"]["resolved_model_config"]["options"]
        == effective["student"]["options"]
    )
    assert config["problem"]["learning_objective"] == ""
    assert "PRIVATE transcript" in json.dumps(archive)
    assert "PRIVATE video" not in json.dumps(saved)
    assert "PRIVATE transcript" not in json.dumps(saved)
    codes = {r["code"] for r in saved["review_reasons"]}
    assert {
        "privacy_change",
        "bucket_to_rolling",
        "sensitivity_removed",
        "subcall_settings_changed",
        "temperature_not_applied",
        "required",
    } <= codes
    login(api, data.owner)
    assert (
        await api.get(f"/admin/scenarios/{data.scenario.id}")
    ).status_code == 403


@pytest.mark.parametrize(
    "template",
    [
        "{unknown}",
        "{prompt.__class__}",
        "{prompt[0]}",
        "{prompt!r}",
        "{prompt:>10}",
        "{prompt:} {prompt}",
        "{",
        "}",
        "oversize",
    ],
)
async def test_unsupported_templates_stay_archived_and_block_publication(
    data, api, legacy, effective, template
):
    from src.models import Scenario

    async with data.factory() as db:
        scenario = await db.get(Scenario, data.scenario.id)
        original = await db.get(PromptTemplate, scenario.student_template_id)
        if template == "oversize":
            template = "{prompt}{prompt}"
            scenario.prompt = "x" * 25001
        else:
            template = "Legacy text " + template
        original.template_text = template
        await db.commit()
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        row = manifest["scenarios"][0]
        assert row["target"]["config"]["student"]["behavior_instruction"] == ""
        assert any(
            r["path"] == "student.behavior_instruction" and r["blocking"]
            for r in row["review_reasons"]
        )
        assert (
            archive["scenarios"][0]["student_template"]["template_text"]
            == template
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
    path = f"/admin/scenarios/{data.scenario.id}"
    saved = (await api.get(path)).json()
    saved.update(
        expected_version=saved["config_version"],
        action="publish",
        acknowledge_review=True,
    )
    body = {
        key: saved[key]
        for key in (
            "title",
            "subject",
            "target_grade",
            "groups",
            "config_schema_version",
            "config",
            "expected_version",
            "action",
            "acknowledge_review",
        )
    }
    response = await post(api, path + "/update", body)
    assert response.status_code == 422
    assert (await api.get(path)).json()["status"] == "draft"


async def test_reexecution_reports_skip_or_conflict_without_overwriting_native_edits(
    data, api, legacy, effective
):
    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        assert (await apply_manifest(db, archive, manifest))[0][
            "status"
        ] == "converted"
        await db.commit()
        assert (await apply_manifest(db, archive, manifest))[0][
            "status"
        ] == "skipped"
        await db.commit()
    path = f"/admin/scenarios/{data.scenario.id}"
    saved = (await api.get(path)).json()
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
    body.update(
        title="Edited native",
        expected_version=saved["config_version"],
        action="save_draft",
    )
    assert (await post(api, path + "/update", body)).status_code == 200
    edited = (await api.get(path)).json()
    async with data.factory() as db:
        assert (await apply_manifest(db, archive, manifest))[0][
            "status"
        ] == "conflict"
        await db.commit()
    assert (await api.get(path)).json() == edited


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "corrupt_rubric",
        "unknown_option",
        "unknown_model",
        "oversize_profile",
    ],
)
async def test_damaged_and_missing_source_becomes_minimal_typed_review_draft(
    data, api, legacy, effective, damage
):
    from src.models import Scenario

    async with data.factory() as db:
        scenario = await db.get(Scenario, data.scenario.id)
        if damage == "missing":
            scenario.student_template_id = None
            scenario.tutor_template_id = None
            scenario.student_profile = None
            scenario.problem_situation = None
            scenario.framework_id = None
        elif damage == "corrupt_rubric":
            from sqlalchemy import text

            await db.execute(
                text(
                    "UPDATE analysis_framework SET labels_json='{broken' WHERE id=:id"
                ),
                {"id": data.framework.id},
            )
        elif damage == "unknown_option":
            effective["student"]["options"]["unrecognized"] = "old setting"
        elif damage == "unknown_model":
            scenario.chat_model = "unregistered-exact-model"
        else:
            scenario.student_profile = "Oversize private " + "x" * 50001
        await db.commit()
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
    saved = (await api.get(f"/admin/scenarios/{data.scenario.id}")).json()
    assert saved["status"] == "draft" and saved["review_required"]
    config = saved["config"]
    if damage == "missing":
        assert config["problem"]["public_text"] == ""
        assert config["mentor"]["mode"] == "off"
        assert config["student"]["behavior_instruction"] == ""
        assert config["analysis"]["rubric"] == []
    elif damage == "corrupt_rubric":
        assert config["analysis"]["rubric"] == []
    elif damage in ("unknown_model", "unknown_option"):
        assert config["student"]["resolved_model_config"] is None
    else:
        assert config["student"]["internal_profile"] == ""
        assert config["student"]["behavior_instruction"] == ""
        assert archive["scenarios"][0]["student_profile"].endswith("x" * 50001)
        assert "발췌" in json.dumps(saved, ensure_ascii=False)


@pytest.mark.parametrize(
    "secret_path",
    [
        "api_key",
        "student.options.password",
        "mentor_judgment.options.master_key",
    ],
)
async def test_effective_input_never_archives_secret_fields(
    data, api, legacy, effective, secret_path
):
    target = effective
    keys = secret_path.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = "SECRET sentinel"
    async with data.factory() as db:
        archive = await capture_archive(db)
        with pytest.raises(ValueError) as error:
            await conversion_manifest(
                db, archive, effective, "private/archive.json"
            )
        assert "SECRET sentinel" not in str(error.value)


async def test_offline_cli_uses_private_immutable_artifacts_and_refuses_tampering(
    data, api, legacy, effective, tmp_path
):
    from src.db.convert_scenarios import convert_copy

    settings_path = tmp_path / "effective.json"
    settings_path.write_text(json.dumps(effective))
    archive_path, manifest_path = (
        tmp_path / "source.json",
        tmp_path / "manifest.json",
    )
    database = tmp_path / "copy.db"
    import sqlite3

    async with data.engine.connect() as conn:
        source_path = conn.engine.url.database
    with (
        sqlite3.connect(source_path) as original,
        sqlite3.connect(database) as copy,
    ):
        original.backup(copy)
    assert (
        await convert_copy(database, settings_path, archive_path, manifest_path)
        == []
    )
    assert archive_path.stat().st_mode & 0o777 == 0o600
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    archive_bytes, manifest_bytes = (
        archive_path.read_bytes(),
        manifest_path.read_bytes(),
    )
    assert (
        await convert_copy(database, settings_path, archive_path, manifest_path)
        == []
    )
    assert archive_path.read_bytes() == archive_bytes
    assert manifest_path.read_bytes() == manifest_bytes
    assert (
        await convert_copy(
            database, settings_path, archive_path, manifest_path, apply=True
        )
    )[0]["status"] == "converted"
    assert (
        await convert_copy(
            database, settings_path, archive_path, manifest_path, apply=True
        )
    )[0]["status"] == "skipped"
    manifest = json.loads(manifest_bytes)
    manifest["scenarios"][0]["target"]["title"] = "Tampered"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest_mismatch"):
        await convert_copy(
            database, settings_path, archive_path, manifest_path, apply=True
        )
    assert (
        await api.get(f"/admin/scenarios/{data.scenario.id}")
    ).status_code == 422, "the source database was never converted"


async def test_admin_review_connects_archive_reference_hashes_and_reasons(
    data, api, legacy, effective
):
    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
    response = await api.get(f"/admin/scenarios/{data.scenario.id}/edit")
    assert response.status_code == 200
    review = response.text.split('class="scenario-layout"')[0]
    assert "private/archive.json" in review
    assert manifest["archive_hash"] in review
    assert manifest["scenarios"][0]["target_hash"] in review
    assert "PRIVATE video" not in response.text
    assert "PRIVATE transcript" not in response.text


async def test_every_legacy_identity_and_historical_record_survives_conversion(
    data, api, legacy, effective
):
    from datetime import datetime

    from sqlalchemy import text

    from src.models import (
        Message,
        QuestionAnalysis,
        Scenario,
        ScenarioGroup,
        SessionFeedbackReport,
        SessionSummary,
    )

    async with data.factory() as db:
        original = await db.get(Scenario, data.scenario.id)
        hidden = Scenario(
            title="Deleted legacy",
            is_active=0,
            deleted_at=datetime(2025, 1, 1),
            student_template_id=original.student_template_id,
            framework_id=original.framework_id,
        )
        db.add(hidden)
        await db.flush()
        db.add(
            ScenarioGroup(scenario_id=hidden.id, group_id=data.owner.group_id)
        )
        message = Message(
            session_id=data.session.id,
            role="teacher",
            content="Original dialogue {x}",
        )
        db.add(message)
        await db.flush()
        db.add_all(
            [
                QuestionAnalysis(
                    message_id=message.id,
                    label="Original label",
                    grade="우수",
                    confidence=0.8,
                    meta_json='{"reason":"Original evidence"}',
                ),
                SessionSummary(
                    session_id=data.session.id,
                    distribution_json='{"Original label":1}',
                    feedback="Original successful feedback",
                ),
                SessionFeedbackReport(
                    session_id=data.session.id,
                    model="original-model",
                    prompt_hash="a" * 64,
                    status="ok",
                    payload_json='{"brief_feedback":"Preserved"}',
                ),
            ]
        )
        await db.commit()
        historical_tables = (
            "session",
            "message",
            "question_analysis",
            "session_summary",
            "session_feedback_report",
        )
        before = {
            table: (await db.execute(text(f"SELECT * FROM {table}"))).all()
            for table in historical_tables
        }
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        results = await apply_manifest(db, archive, manifest)
        await db.commit()
        assert results == [
            dict(id=data.scenario.id, status="converted"),
            dict(id=hidden.id, status="converted"),
        ]
        after = await capture_archive(db)
        for old, current in zip(
            archive["scenarios"], after["scenarios"], strict=True
        ):
            assert current == old | {
                "config_version": old["config_version"] + 1
            }
        assert before == {
            table: (await db.execute(text(f"SELECT * FROM {table}"))).all()
            for table in historical_tables
        }
        assert (
            await db.get(Scenario, hidden.id, populate_existing=True)
        ).status == "draft"
        assert (await db.get(Scenario, hidden.id)).review_required


async def test_utf8_oversize_target_stays_archived_and_can_be_saved_as_minimal_draft(
    data, api, legacy, effective
):
    from sqlalchemy import text

    async with data.factory() as db:
        labels = [
            dict(name=f"Row {i}", criteria="한" * 50000) for i in range(20)
        ]
        await db.execute(
            text(
                "UPDATE analysis_framework SET labels_json=:labels WHERE id=:id"
            ),
            dict(labels=json.dumps(labels), id=data.framework.id),
        )
        await db.commit()
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        assert any(
            r["code"] == "oversize_target" and r["blocking"]
            for r in manifest["scenarios"][0]["review_reasons"]
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
    path = f"/admin/scenarios/{data.scenario.id}"
    saved = (await api.get(path)).json()
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
    assert (await post(api, path + "/update", body)).status_code == 200
    assert (
        json.loads(archive["scenarios"][0]["framework"]["labels_json"])[19][
            "criteria"
        ]
        == "한" * 50000
    )


async def test_missing_profile_fallback_is_provenance_not_an_inferred_grade(
    data, api, legacy, effective
):
    from src.models import Scenario

    async with data.factory() as db:
        scenario = await db.get(Scenario, data.scenario.id)
        scenario.student_profile = None
        scenario.tutor_template_id = None
        await db.commit()
        effective["mentor_coaching"] = None
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        row = manifest["scenarios"][0]
        assert (
            "Grade 5 student"
            in row["target"]["config"]["student"]["behavior_instruction"]
        )
        assert row["target"]["target_grade"] == ""
        assert row["target"]["config"]["student"]["internal_profile"] == ""
        assert row["target"]["config"]["student"]["public_profile"] == ""
        assert any(
            "실제 학년 아님" in str(e["target"])
            for e in row["conversion_provenance"]
        )
        assert not any(
            r["path"] == "mentor.resolved_model_config" and r["blocking"]
            for r in row["review_reasons"]
        )


async def test_changed_legacy_source_is_reported_as_conflict(
    data, api, legacy, effective
):
    from src.models import Scenario

    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        scenario = await db.get(Scenario, data.scenario.id)
        scenario.prompt = "Changed after dry run"
        await db.commit()
        assert (await apply_manifest(db, archive, manifest))[0][
            "status"
        ] == "conflict"
        await db.commit()
        assert (await capture_archive(db))["scenarios"][0][
            "prompt"
        ] == "Changed after dry run"
    assert (
        await api.get(f"/admin/scenarios/{data.scenario.id}")
    ).status_code == 422


async def test_exact_student_override_uses_captured_options_and_accurate_source_evidence(
    data, api, legacy, effective
):
    from src.models import ModelConfig, Scenario

    async with data.factory() as db:
        model = ModelConfig(
            provider_connection_id=1,
            model_id="gpt-5.2",
            display_name="Exact override",
            default_options_json={"max_output_tokens": 2048},
        )
        db.add(model)
        scenario = await db.get(Scenario, data.scenario.id)
        scenario.chat_model = "gpt-5.2"
        await db.commit()
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        row = manifest["scenarios"][0]
        selection = row["target"]["config"]["student"]["resolved_model_config"]
        assert selection["model_id"] == "gpt-5.2"
        assert selection["model_config_id"] == model.id
        assert selection["options"] == effective["student"]["options"]
        evidence = next(
            e
            for e in row["conversion_provenance"]
            if e["field"] == "student.resolved_model_config"
        )
        assert evidence["source"]["model_id"] == "gpt-5.2"
        assert (
            manifest["effective_settings"]["student"]["model_id"]
            == "gpt-5-mini"
        )

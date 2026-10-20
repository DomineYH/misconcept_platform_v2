"""D2-03 seed builder: official installer, receipt, seed tuple and tour content."""

import asyncio
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_demo_d2_03_fixtures import D0_02
from test_demo_d2_03_fixtures import INPUTS as FIXTURE_INPUTS

from src.api.routes.admin_session_actions import (
    delete_session,
    get_admin_analysis,
)
from src.api.routes.admin_session_export import (
    download_session,
    export_selected_sessions,
)
from src.api.routes.admin_users import delete_user
from src.api.schemas.ai_settings import CallLimits
from src.demo import seed
from src.demo.database import DatabaseRoot, DemoError
from src.models import GenerationRun, Message, QuestionAnalysis, Session, User
from src.services.analysis_results import load_analysis_response
from src.services.export import CSVExporter
from src.services.lesson_snapshots import read_lesson_snapshot
from src.services.model_verification import effective_roles

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(seed.MANIFEST.read_text())


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed")
    receipt = asyncio.run(seed.build(out))
    return out, receipt


@pytest.fixture
def seed_db(built):
    db = sqlite3.connect(f"file:{built[0] / seed.SEED_NAME}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    yield db
    db.close()


def test_inputs_and_migration_tuple_match_fixtures_and_installer(seed_db):
    assert seed.INPUTS == FIXTURE_INPUTS
    assert all(value in D0_02 for value in seed.INPUTS.values())
    assert seed.migration_set_sha256() == MANIFEST["migration_set_sha256"]
    assert MANIFEST["db_schema_version"] == seed.DB_SCHEMA_VERSION
    history = tuple(
        r[0]
        for r in seed_db.execute("SELECT filename FROM _migrations ORDER BY id")
    )
    assert history == seed.migration_filenames()
    assert history[-1] == seed.DB_SCHEMA_VERSION


def test_receipt_records_real_values_and_logical_data_is_deterministic(
    built, tmp_path
):
    out, receipt = built
    assert json.loads((out / seed.RECEIPT_NAME).read_text()) == receipt
    assert list(receipt) == [
        "source_commit",
        "manifest_sha256",
        "db_schema_version",
        "seed_sha256",
        "logical_data_sha256",
        "command",
    ]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert receipt["source_commit"] == head and len(head) == 40
    assert receipt["manifest_sha256"] == _sha(seed.MANIFEST.read_bytes())
    assert receipt["db_schema_version"] == "037_demo_provider.sql"
    assert receipt["seed_sha256"] == _sha((out / seed.SEED_NAME).read_bytes())
    assert receipt["command"] == f"python -m src.demo.seed build --out {out}"
    again = asyncio.run(seed.build(tmp_path))
    # A fresh bcrypt salt changes only physical bytes; rows are pinned.
    assert again["seed_sha256"] != receipt["seed_sha256"]
    assert (
        again["logical_data_sha256"]
        == receipt["logical_data_sha256"]
        == seed.SEED_LOGICAL_SHA256
    )
    assert not list(tmp_path.glob("*.staging"))


def test_rows_are_keyless_demo_with_current_role_evidence(seed_db):
    refs = MANIFEST["seed_refs"]
    connections = [
        dict(r) for r in seed_db.execute("SELECT * FROM provider_connection")
    ]
    assert [c["provider"] for c in connections] == [
        "openai",
        "anthropic",
        "google",
        "demo",
    ]
    demo = connections[-1]
    assert demo["id"] == refs["connection"]
    assert all(
        c[k] is None
        for c in connections
        for k in (
            "encrypted_key",
            "nonce",
            "encryption_key_version",
            "masked_hint",
        )
    )
    assert [c["enabled"] for c in connections] == [0, 0, 0, 1]
    assert (demo["credential_revision"], demo["connection_version"]) == (0, 1)
    audit = [
        dict(r) for r in seed_db.execute("SELECT * FROM provider_audit_log")
    ]
    assert [
        (a["change_kind"], a["actor_id"], a["provider"]) for a in audit
    ] == [("demo_seeded", None, "demo")]
    models = [dict(r) for r in seed_db.execute("SELECT * FROM model_config")]
    assert len(models) == 1 and models[0]["id"] == refs["model"]
    model = SimpleNamespace(
        **{
            k: json.loads(v) if k.endswith(("_json", "_state")) else v
            for k, v in models[0].items()
        }
    )
    assert (model.model_id, model.capability_definition_version) == (
        "demo-classroom-v1",
        "demo-cap-v1",
    )
    assert model.default_options_json == {"max_output_tokens": 4096}
    roles = effective_roles(model, SimpleNamespace(**demo), app_mode="demo")
    assert {role: state["status"] for role, state in roles.items()} == dict(
        student="succeeded", mentor="succeeded", analysis="succeeded"
    )
    setting = dict(seed_db.execute("SELECT * FROM app_setting").fetchone())
    # Contract §8 demo seed values.
    assert json.loads(setting["limits_json"]) == dict(
        total=2, openai=2, anthropic=2, google=2, admin=1, demo=2
    )
    assert json.loads(setting["timeouts_json"]) == dict(
        connect=2,
        student_first_output=5,
        student_total=15,
        mentor_first_output=5,
        mentor_total=15,
        analysis_total=30,
        model_list_total=5,
    )
    CallLimits.model_validate(json.loads(setting["limits_json"]))
    users = [dict(r) for r in seed_db.execute("SELECT * FROM user ORDER BY id")]
    assert [(u["id"], u["role"]) for u in users] == [
        (refs["admin"], "admin"),
        (refs["teacher"], "teacher"),
    ]
    assert all(u["password_hash"].startswith("$2") for u in users)
    assert {
        r[0]
        for r in seed_db.execute("SELECT DISTINCT provider FROM generation_run")
    } == {"demo"}
    usage = [dict(r) for r in seed_db.execute("SELECT * FROM api_usage_log")]
    assert [u["operation"] for u in usage] == [
        "student",
        "mentor",
        "student",
        "mentor",
        "analysis_unified",
    ]
    assert all(
        u["usage_complete"] == 0 and u[k] is None
        for u in usage
        for k in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_cost_usd",
        )
    )
    stamps = [
        r[0]
        for table, column in (
            ("session", "started_at"),
            ("message", "created_at"),
            ("generation_run", "finished_at"),
            ("user", "created_at"),
            ("provider_connection", "updated_at"),
            ("app_setting", "updated_at"),
        )
        for r in seed_db.execute(f'SELECT {column} FROM "{table}"')
    ]
    assert all(s.startswith("2026-10-18 00:") for s in stamps)
    assert {
        r[0] for r in seed_db.execute("SELECT applied_at FROM _migrations")
    } == {"2026-10-18 00:00:00"}


def test_completed_example_follows_the_default_b_path(seed_db):
    sid = MANIFEST["seed_refs"]["completed_session"]
    messages = [
        (r["role"], r["content"])
        for r in seed_db.execute(
            "SELECT role, content FROM message WHERE session_id=? ORDER BY created_at, id",
            (sid,),
        )
    ]
    assert [m[0] for m in messages] == [
        "teacher",
        "student",
        "tutor",
        "teacher",
        "student",
    ]
    assert (messages[0][1], messages[3][1]) == (
        seed.INPUTS["B"],
        seed.INPUTS["common"],
    )
    runs = [
        (r["operation"], r["status"], r["result_kind"], r["mentor_trigger"])
        for r in seed_db.execute(
            "SELECT * FROM generation_run ORDER BY started_at, id"
        )
    ]
    assert runs == [
        ("student", "completed", "message", None),
        ("mentor", "completed", "message", "auto"),
        ("student", "completed", "message", None),
        ("mentor", "completed", "no_intervention", "auto"),
        ("analysis", "ok", None, None),
    ]
    labels = [
        r[0]
        for r in seed_db.execute(
            "SELECT q.label FROM question_analysis q JOIN message m ON m.id=q.message_id ORDER BY m.id"
        )
    ]
    assert labels == ["r2", "r3"]
    session = dict(
        seed_db.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    )
    assert (
        session["tutor_question_count"],
        session["tutor_intervention_count"],
        session["snapshot_origin"],
    ) == (2, 1, "native")


@pytest.fixture
def root(tmp_path):
    root = DatabaseRoot(tmp_path / "demo", initialize=True).open()
    yield root
    root.close()


async def test_validate_seed_and_clone_accept_the_built_seed(built, root):
    spec, _ = seed.load_seed(built[0])
    root.validate_seed(spec)
    ref = await root.clone(str(uuid4()), 1)
    try:
        async with ref.factory() as db:
            assert (await db.scalar(text("SELECT count(*) FROM message"))) == 5
            assert (
                await db.scalar(text("SELECT kind FROM demo_metadata"))
            ) == "demo_visitor"
    finally:
        await ref.factory.dispose()


def _snapshot_model(db):
    from src.services.lesson_snapshots import canonical_hash

    (raw,) = db.execute("SELECT config_snapshot_json FROM session").fetchone()
    envelope = json.loads(raw)
    envelope["config"]["student"]["resolved_model_config"].update(
        provider="openai", model_id="gpt-4.1"
    )
    db.execute(
        "UPDATE session SET config_snapshot_json=?, config_hash=?",
        (json.dumps(envelope, ensure_ascii=False), canonical_hash(envelope)),
    )


# Value mutations a sparse row check would miss (review of PR #228).
MUTATIONS = dict(
    seed_row="DELETE FROM provider_audit_log",
    message_text="UPDATE message SET content=content||' 수정' WHERE id=2",
    model_evidence="UPDATE model_config SET verification_state='{}'",
    measured_usage="UPDATE api_usage_log SET input_tokens=10, output_tokens=5, "
    "total_tokens=15, usage_complete=1 WHERE id=1",
    scenario_instruction="UPDATE scenario SET config_json=json_set(config_json,"
    "'$.student.behavior_instruction','다른 지시')",
    snapshot_model=_snapshot_model,
)


@pytest.mark.parametrize(
    "tamper",
    ["migration_set", "schema_version", "fixture_byte", "history", *MUTATIONS],
)
def test_any_seed_tuple_change_is_version_mismatch(
    built, root, tmp_path, tamper
):
    spec, _ = seed.load_seed(built[0])
    if tamper == "migration_set":
        spec = replace(spec, migration_set_sha256="0" * 64)
    elif tamper == "schema_version":
        spec = replace(
            spec, db_schema_version="036_comparison_conversation.sql"
        )
    elif tamper == "history":
        spec = replace(spec, migration_filenames=spec.migration_filenames[:-1])
    elif tamper == "fixture_byte":
        fixtures = tmp_path / "fixtures"
        shutil.copytree(seed.FIXTURES, fixtures)
        target = fixtures / "student/initial-B-B.json"
        target.write_bytes(target.read_bytes().replace(b"1/4", b"1/5", 1))
        spec = replace(spec, manifest_path=fixtures / "manifest.json")
    else:
        copy = tmp_path / seed.SEED_NAME
        shutil.copy(spec.path, copy)
        mutation = MUTATIONS[tamper]
        with sqlite3.connect(copy) as db:
            if callable(mutation):
                mutation(db)
            else:
                assert db.execute(mutation).rowcount >= 1
        db.close()
        spec = replace(spec, path=copy)
    with pytest.raises(DemoError) as error:
        root.validate_seed(spec)
    assert error.value.code == "DEMO_VERSION_MISMATCH"


def test_build_refuses_to_publish_fixture_bytes_startup_rejects(
    tmp_path, monkeypatch
):
    fixtures = tmp_path / "fixtures"
    shutil.copytree(seed.FIXTURES, fixtures)
    target = fixtures / "student/initial-B-B.json"
    target.write_bytes(target.read_bytes() + b" ")
    monkeypatch.setattr(seed, "FIXTURES", fixtures)
    monkeypatch.setattr(seed, "MANIFEST", fixtures / "manifest.json")
    out = tmp_path / "out"
    with pytest.raises(DemoError) as error:
        asyncio.run(seed.build(out))
    assert error.value.code == "DEMO_VERSION_MISMATCH"
    assert sorted(p.name for p in out.iterdir()) == []


def test_build_accepts_a_relative_out_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    receipt = asyncio.run(seed.build("out"))
    assert (tmp_path / "out" / seed.SEED_NAME).is_file()
    assert receipt["logical_data_sha256"] == seed.SEED_LOGICAL_SHA256


def test_restart_recovery_accepts_edited_visitor_databases(built, tmp_path):
    # #191 re-verifies leftover visitor DBs with this hook; visitors edit them.
    copy = tmp_path / "visitor.db"
    shutil.copy(built[0] / seed.SEED_NAME, copy)
    with sqlite3.connect(copy) as db:
        db.execute(
            "UPDATE demo_metadata SET kind='demo_visitor', visitor_id='v', "
            "generation=1"
        )
        db.execute("DELETE FROM user WHERE id=2")
    db.close()
    spec, _ = seed.load_seed(built[0])
    with sqlite3.connect(copy) as db:
        spec.verify_content(db, MANIFEST)
    db.close()


def _session_row(seed_db, **changes):
    row = dict(
        seed_db.execute(
            "SELECT id, deleted_at, ended_at, config_snapshot_json, config_hash, "
            "snapshot_origin, source_scenario_version, snapshot_created_at "
            "FROM session WHERE id=1"
        ).fetchone()
    )
    return row | changes


def test_tour_content_exposes_manifest_refs_and_supported_snapshots(
    built, seed_db
):
    from src.services.lesson_snapshots import canonical_hash

    _, tour = seed.load_seed(built[0])
    assert tour.seed_refs == MANIFEST["seed_refs"]
    assert all(type(v) is int and v > 0 for v in tour.seed_refs.values())
    assert tour.first_inputs == frozenset(seed.INPUTS[k] for k in "ABC")
    assert tour.common_input == seed.INPUTS["common"]
    row = _session_row(seed_db)
    assert tour.supported_snapshot(row)
    envelope = json.loads(row["config_snapshot_json"])
    envelope["scenario_context"]["title"] = "제목만 바꾼 시나리오"
    for role in ("student", "mentor", "analysis"):
        envelope["config"][role]["resolved_model_config"]["options"] = {
            "max_output_tokens": 8192
        }
    assert tour.supported_snapshot(
        row
        | dict(
            config_snapshot_json=envelope, config_hash=canonical_hash(envelope)
        )
    )
    envelope["config"]["student"]["behavior_instruction"] += " "
    for rejected in (
        row | dict(snapshot_origin="legacy_reconstructed"),
        row | dict(config_hash="0" * 64),
        row
        | dict(
            config_snapshot_json=envelope, config_hash=canonical_hash(envelope)
        ),
        row | dict(config_snapshot_json="not json"),
    ):
        assert not tour.supported_snapshot(rejected)


DEMO_ENV = {
    "APP_MODE": "demo",
    "DEMO_DATA_ROOT": "/nonexistent/isolated-demo-root",
    "DEMO_SEED_DIR": "/nonexistent/isolated-demo-seed",
    "DEMO_ORIGIN": "https://demo.invalid",
    "NORMAL_ORIGIN": "https://normal.invalid",
    "DEMO_SESSION_SECRET": "s" * 40,
    "DEMO_CSRF_SECRET": "c" * 40,
}


def run_cli(tmp_path, env, *args):
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("DEMO_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_")
        )
        and k
        not in {"APP_MODE", "TESTING", "DATABASE_URL", "SESSION_SECRET", "ENV"}
    }
    return subprocess.run(
        [sys.executable, "-m", "src.demo.seed", *args],
        env=clean | dict(PYTHONPATH=str(ROOT)) | env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_cli_builds_only_in_demo_mode_without_dotenv_or_key_env(tmp_path):
    # A .env with a key would be absorbed by normal-mode config; demo ignores it.
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=forbidden\nAPP_MODE=normal\n"
    )
    out = tmp_path / "out"
    refused = run_cli(tmp_path, {}, "build", "--out", str(out))
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["code"] == "DEMO_CONFIGURATION_INVALID"
    keyed = run_cli(
        tmp_path,
        DEMO_ENV | dict(OPENAI_API_KEY="forbidden"),
        "build",
        "--out",
        str(out),
    )
    assert keyed.returncode != 0 and not out.exists()
    built = run_cli(tmp_path, DEMO_ENV, "build", "--out", str(out))
    assert built.returncode == 0, built.stderr
    receipt = json.loads((out / seed.RECEIPT_NAME).read_text())
    assert json.loads(built.stdout) == receipt
    assert receipt["command"] == f"python -m src.demo.seed build --out {out}"
    assert receipt["seed_sha256"] == _sha((out / seed.SEED_NAME).read_bytes())


# Existing readers, exports and deletes work on the built seed (§17/§22/§23).
# D0-02 section 8: existing admin CSV header order.
ADMIN_HEADER = (
    "session_id,scenario_id,scenario_title,teacher_id,teacher_username,"
    "teacher_nickname,session_started_at,session_ended_at,message_id,"
    "message_created_at,role,content,label,confidence,meta_json,feedback,"
    "classification_status,student_name,snapshot_origin,snapshot_created_at,"
    "source_scenario_version,config_hash_kind,analysis_schema_version,"
    "analysis_status,analysis_coverage_json,misconception_findings_json,"
    "message_analysis_disposition"
).split(",")
ROLES = ["teacher", "student", "tutor", "teacher", "student", "summary"]


@pytest_asyncio.fixture
async def db(built, tmp_path):
    copy = tmp_path / "seed.db"
    copyfile(built[0] / seed.SEED_NAME, copy)
    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def rows(content):
    return list(csv.DictReader(io.StringIO(content)))


async def test_lesson_snapshot_reads_with_demo_model(db):
    session = await db.get(Session, 1)
    lesson = read_lesson_snapshot(session)
    assert session.snapshot_origin == "native"
    for section in ("student", "mentor", "analysis"):
        assert getattr(
            lesson.config, section
        ).resolved_model_config.model_dump() == dict(
            model_config_id=1,
            provider_connection_id=4,
            provider="demo",
            model_id="demo-classroom-v1",
            options=dict(max_output_tokens=4096),
        )


async def test_teacher_analysis_response_matches_seed(db):
    response = await load_analysis_response(1, db, admin=False)
    assert response["feedback_status"] == "ok"
    assert [q["label"] for q in response["questions"]] == ["r2", "r3"]
    assert response["distribution"] == {"r1": 0, "r2": 1, "r3": 1}
    accepted = response["accepted_report"]
    assert response["feedback"] == accepted["brief_feedback"][0]
    coverage = accepted["coverage"]
    assert len(coverage["input_message_ids"]) == 4
    assert len(coverage["reviewed_message_ids"]) == 4
    assert len(coverage["teacher_message_ids"]) == 2
    assert len(coverage["classified_message_ids"]) == 2
    assert not coverage["missing_message_ids"]
    assert not coverage["invalid_message_ids"]


async def test_admin_csv_has_expected_shape(db):
    content = await CSVExporter().export_session_admin(1, db)
    parsed = rows(content)
    assert list(parsed[0]) == ADMIN_HEADER
    assert [r["role"] for r in parsed] == ROLES
    assert {r["analysis_schema_version"] for r in parsed} == {"2"}
    assert {r["analysis_status"] for r in parsed} == {"ok"}
    for row in parsed[:5]:
        assert row["confidence"] == ""
        assert row["message_analysis_disposition"] == (
            "classified" if row["role"] == "teacher" else ""
        )
    reasons = await db.scalars(
        select(GenerationRun.mentor_reason_summary).where(
            GenerationRun.operation == "mentor"
        )
    )
    reasons = [r for r in reasons if r]
    assert reasons and not any(r in content for r in reasons)


async def test_delete_teacher_keeps_completed_session(db):
    admin = await db.get(User, 1)
    result = await delete_user(2, admin, db, None)
    await db.commit()
    assert result == {"status": "deleted", "user_id": 2}
    assert await db.scalar(select(User.id).where(User.id == 2)) is None
    assert (
        await db.scalar(select(Session.teacher_id).where(Session.id == 1))
        is None
    )
    assert await db.scalar(select(func.count(Message.id))) == 5
    assert await db.scalar(select(func.count(QuestionAnalysis.id))) == 2
    assert (await load_analysis_response(1, db))["feedback_status"] == "ok"
    first = rows(await CSVExporter().export_session_admin(1, db))[0]
    assert first["teacher_id"] == first["teacher_username"] == ""


async def test_soft_deleted_session_is_hidden_from_admin_routes(db):
    admin = await db.get(User, 1)
    await delete_session(1, admin, db, None)
    await db.commit()
    assert (await db.get(Session, 1)).deleted_at is not None
    for call in (
        get_admin_analysis(1, admin, db, None),
        download_session(1, admin, db, None),
    ):
        with pytest.raises(HTTPException) as error:
            await call
        assert error.value.status_code == 404
    selected = await export_selected_sessions([1], admin, db, None)
    assert selected.media_type == "text/plain"
    assert "Session Summary" not in selected.body.decode()

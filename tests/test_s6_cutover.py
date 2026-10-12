"""S6 CLI rehearsal uses synthetic isolated copies and immutable evidence."""

import json
import sqlite3
from pathlib import Path

import pytest
from test_s2_cutover import source as legacy_source

from src.db.s2_cutover import rehearse as rehearse_s2
from src.db.s2_verification import database_state, readonly
from src.db.s6_verification import upgrade

__all__ = ["legacy_source"]


@pytest.fixture
async def current_source(legacy_source, tmp_path, request):
    workspace = tmp_path / "s2"
    await rehearse_s2(
        legacy_source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    source = workspace / "rehearsal.db"
    await upgrade(source, getattr(request, "param", 35))
    return source


async def test_empty_dry_run_keeps_source_and_candidate_empty(tmp_path):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    before = source.read_bytes()
    workspace = tmp_path / "workspace"
    report = await rehearse(source, workspace)
    assert report["status"] == "dry_run"
    assert report["release_blocked"] is True
    assert report["source_revision"] == 0
    assert source.read_bytes() == before
    with sqlite3.connect(workspace / "candidate.db") as db:
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert await rehearse(source, workspace) == report


async def test_empty_apply_migrates_twice_and_restores_both_versions(tmp_path):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    workspace = tmp_path / "workspace"
    await rehearse(source, workspace)
    report = await rehearse(source, workspace, apply=True)
    assert report["status"] == "ready"
    assert report["candidate_revision"] == 35
    assert report["checks"] == {
        "integrity": True,
        "foreign_keys": True,
        "preserved": True,
        "restore_source": True,
        "restore_candidate": True,
        "migration_idempotence": True,
        "fresh_schema": True,
    }
    assert report["release_blocked"] is True
    for name in ("candidate.db", "restore-candidate.db"):
        with sqlite3.connect(workspace / name) as db:
            assert db.execute(
                "SELECT filename FROM _migrations ORDER BY id DESC LIMIT 1"
            ).fetchone() == ("035_comparison_run.sql",)
    assert await rehearse(source, workspace, apply=True) == report


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize("current_source", range(31, 36), indirect=True)
async def test_official_current_revisions_preserve_all_original_values(
    current_source, tmp_path
):
    from src.db.s6_cutover import rehearse

    with readonly(current_source) as db:
        original = database_state(db)
        revision = int(
            db.execute(
                "SELECT filename FROM _migrations ORDER BY id DESC LIMIT 1"
            ).fetchone()[0][:3]
        )
    workspace = tmp_path / "s6"
    report = await rehearse(current_source, workspace, apply=True)
    assert report["source_revision"] == revision
    assert report["status"] == "ready"
    assert report["preserved"]["message"]["count"] == 1
    assert report["preserved"]["scenario"] == original["tables"]["scenario"]
    for name in ("backup.db", "restore-source.db"):
        with readonly(workspace / name) as db:
            assert database_state(db) == original
    with readonly(current_source) as db:
        assert database_state(db) == original
    with readonly(workspace / "candidate.db") as db:
        assert db.execute(
            "SELECT content FROM message WHERE id=40"
        ).fetchone() == ("Original {text}",)
        assert db.execute(
            "SELECT total_tokens,estimated_cost_usd FROM api_usage_log WHERE id=44"
        ).fetchone() == (12, 0.012345)
    assert await rehearse(current_source, workspace, apply=True) == report


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "artifact",
    [
        "source",
        "candidate.db",
        "backup.db",
        "restore-source.db",
        "restore-candidate.db",
        "dry-run.json",
        "report.json",
        "receipt.json",
    ],
)
async def test_completed_rehearsal_refuses_changed_inputs_and_evidence(
    current_source, tmp_path, artifact
):
    from src.db.s6_cutover import rehearse

    workspace = tmp_path / "s6"
    await rehearse(current_source, workspace, apply=True)
    path = current_source if artifact == "source" else workspace / artifact
    if path.suffix == ".db":
        with sqlite3.connect(path) as db:
            db.execute("UPDATE message SET content='Synthetic tampering'")
            db.commit()
    else:
        path.write_text("{}")
    before = {
        p.name: p.read_bytes()
        for p in workspace.iterdir()
        if p.suffix in {".db", ".json"}
    }
    with pytest.raises(ValueError, match="mismatch|changed"):
        await rehearse(current_source, workspace, apply=True)
    assert all(
        (workspace / name).read_bytes() == value
        for name, value in before.items()
    )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_current_wal_backup_retains_committed_pages_without_source_writes(
    current_source, tmp_path
):
    from src.db.s6_cutover import rehearse

    workspace = tmp_path / "s6"
    with sqlite3.connect(current_source) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE message SET content='WAL_COMMITTED_SENTINEL'")
        writer.commit()
        wal = Path(str(current_source) + "-wal")
        assert wal.stat().st_size > 0
        before = current_source.read_bytes(), wal.read_bytes()
        await rehearse(current_source, workspace, apply=True)
        assert (current_source.read_bytes(), wal.read_bytes()) == before
        for name in (
            "backup.db",
            "candidate.db",
            "restore-source.db",
            "restore-candidate.db",
        ):
            with readonly(workspace / name) as db:
                assert db.execute(
                    "SELECT content FROM message WHERE id=40"
                ).fetchone() == ("WAL_COMMITTED_SENTINEL",)


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize("current_source", [31, 35], indirect=True)
async def test_native_snapshot_is_preserved_alongside_reconstructed_history(
    current_source, tmp_path
):
    from src.api.schemas.scenario_config import ScenarioConfig
    from src.db.s6_cutover import rehearse
    from src.services.lesson_snapshots import canonical_hash

    config = json.loads(Path("tests/fixtures/s2_draft.json").read_text())[
        "config"
    ]
    selection = dict(
        model_config_id=1,
        provider_connection_id=1,
        provider="openai",
        model_id="gpt-5-mini",
        options={"max_output_tokens": 900},
    )
    config["problem"]["learning_objective"] = "Native objective"
    config["student"].update(
        name="Native",
        misconception="Native misconception",
        behavior_instruction="Native instruction",
        resolved_model_config=selection,
    )
    config["analysis"].update(
        context="Native context",
        expected_understanding="Native understanding",
        instruction="Native analysis",
        resolved_model_config=selection,
    )
    config = ScenarioConfig.model_validate(config).model_dump()
    snapshot = dict(
        schema_version=1,
        scenario_context=dict(
            title="Frozen native title", subject="Math", target_grade="Middle"
        ),
        config=config,
    )
    with sqlite3.connect(current_source) as db:
        db.execute(
            "INSERT INTO session(id,scenario_id,teacher_id,started_at,ended_at,tutor_intervention_count,tutor_question_count,config_snapshot_json,config_hash,source_scenario_version,snapshot_origin,snapshot_created_at) VALUES(2,1,1,'2026-01-01','2026-01-02',0,0,?,?,7,'native','2026-01-01')",
            (json.dumps(snapshot), canonical_hash(snapshot)),
        )
        db.commit()
        before = db.execute(
            "SELECT id,config_snapshot_json,config_hash,source_scenario_version,snapshot_origin,snapshot_created_at FROM session ORDER BY id"
        ).fetchall()
    workspace = tmp_path / "s6"
    report = await rehearse(current_source, workspace, apply=True)
    assert report["preserved"]["session"]["count"] == 2
    with readonly(workspace / "candidate.db") as db:
        assert (
            db.execute(
                "SELECT id,config_snapshot_json,config_hash,source_scenario_version,snapshot_origin,snapshot_created_at FROM session ORDER BY id"
            ).fetchall()
            == before
        )

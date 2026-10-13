"""Legacy-to-current rehearsal through the entry seam, on synthetic copies."""

import json
import sqlite3
from pathlib import Path

import pytest

from src.db.s2_cutover import rehearse as rehearse_s2
from src.db.s2_verification import database_state, readonly
from src.db.s6_cutover import rehearse
from src.db.s6_verification import upgrade

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture
async def legacy_input(data, tmp_path, request):
    revision = getattr(request, "param", 23)
    source = Path(data.engine.url.database)
    await upgrade(source, revision)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE scenario SET problem_situation='Public problem'")
        db.execute(
            "INSERT INTO message(id,session_id,role,content,created_at) "
            "VALUES(40,1,'teacher','Original {text}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO session_summary(id,session_id,distribution_json,feedback) "
            "VALUES(41,1,'{}','Original feedback')"
        )
        db.execute(
            "INSERT INTO api_usage_log(id,session_id,bot_type,model,prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd,timestamp) "
            "VALUES(44,1,'student','unknown',0,0,?,?,'2026-01-01')",
            (None, None) if revision >= 28 else (0, 0.0),
        )
    source.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            sidecar.chmod(0o600)
    settings = tmp_path / "settings.json"
    settings.write_bytes(
        Path("tests/fixtures/s2_legacy_effective.json").read_bytes()
    )
    settings.chmod(0o600)
    return source, settings


@pytest.mark.parametrize("legacy_input", [23, 27, 30], indirect=True)
async def test_legacy_rehearsal_seals_s2_then_upgrades_separate_candidate(
    legacy_input, tmp_path
):
    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    with readonly(source) as db:
        before = database_state(db)
    dry = await rehearse(
        source, latest, settings=settings, legacy_workspace=legacy
    )
    assert dry["status"] == "dry_run" and dry["release_blocked"]
    assert not (legacy / "receipt.json").exists()
    assert not (latest / "candidate.db").exists()
    s2 = await rehearse_s2(source, legacy, settings, apply=True)
    sealed = {p.name: p.read_bytes() for p in legacy.iterdir()}
    report = await rehearse(
        source, latest, apply=True, settings=settings, legacy_workspace=legacy
    )
    assert report["status"] == "ready" and report["candidate_revision"] == 36
    assert report["legacy"]["source_revision"] in (23, 27, 30)
    assert report["legacy"]["preserved"]["api_usage_log"]["count"] == 1
    assert report["preserved"]["message"]["count"] == 1
    assert {p.name: p.read_bytes() for p in legacy.iterdir()} == sealed
    assert await rehearse_s2(source, legacy, settings, apply=True) == s2
    assert (
        await rehearse(
            source,
            latest,
            apply=True,
            settings=settings,
            legacy_workspace=legacy,
        )
        == report
    )
    with readonly(source) as db:
        assert database_state(db) == before
    with readonly(latest / "candidate.db") as db:
        assert db.execute(
            "SELECT status,review_required FROM scenario"
        ).fetchone() == ("draft", 1)
        assert db.execute(
            "SELECT snapshot_origin,source_scenario_version FROM session"
        ).fetchone() == ("legacy_reconstructed", None)
        assert db.execute(
            "SELECT model,total_tokens,estimated_cost_usd,run_id,usage_scope FROM api_usage_log"
        ).fetchone() == (
            ("unknown", None, None, None, None)
            if report["legacy"]["source_revision"] >= 28
            else ("unknown", 0, 0.0, None, None)
        )


@pytest.mark.parametrize("legacy_input", [30], indirect=True)
@pytest.mark.parametrize("active", ["session", "run", "usage"])
@pytest.mark.parametrize("apply", [False, True])
async def test_legacy_rehearsal_refuses_unfinished_work(
    legacy_input, tmp_path, active, apply
):
    source, settings = legacy_input
    with sqlite3.connect(source) as db:
        if active == "session":
            db.execute("UPDATE session SET ended_at=NULL")
        elif active == "run":
            db.execute(
                "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at) "
                "VALUES('unfinished',1,1,'turn','student','request','input','config','openai','synthetic','running','2026-01-01')"
            )
        else:
            db.execute(
                "UPDATE api_usage_log SET status='running',started_at='2026-01-01',finished_at=NULL"
            )
    with readonly(source) as db:
        before = database_state(db)
    with pytest.raises(ValueError, match="active_sessions|unfinished_runs"):
        await rehearse(
            source,
            tmp_path / "s6",
            apply=apply,
            settings=settings,
            legacy_workspace=tmp_path / "s2",
        )
    with readonly(source) as db:
        assert database_state(db) == before
    assert not (tmp_path / "s6" / "candidate.db").exists()


@pytest.mark.parametrize("legacy_input", range(23, 31), indirect=True)
async def test_every_official_legacy_schema_boundary_and_custom_schema_refusal(
    legacy_input, tmp_path
):
    source, settings = legacy_input
    dry = await rehearse(
        source,
        tmp_path / "s6",
        settings=settings,
        legacy_workspace=tmp_path / "s2",
    )
    assert dry["status"] == "dry_run"
    with sqlite3.connect(source) as db:
        db.execute("ALTER TABLE message ADD COLUMN custom_value TEXT")
    with pytest.raises(ValueError, match="unsupported_schema"):
        await rehearse(
            source,
            tmp_path / "bad-s6",
            apply=True,
            settings=settings,
            legacy_workspace=tmp_path / "bad-s2",
        )
    assert not (tmp_path / "bad-s2" / "backup.db").exists()


@pytest.mark.parametrize("legacy_input", [30], indirect=True)
@pytest.mark.parametrize(
    "changed",
    [
        "source",
        "settings",
        "manifest.json",
        "receipt.json",
        "report.json",
        "rehearsal.db",
    ],
)
async def test_legacy_completed_evidence_refuses_stale_inputs(
    legacy_input, tmp_path, changed
):
    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    await rehearse(
        source, latest, apply=True, settings=settings, legacy_workspace=legacy
    )
    if changed in {"source", "rehearsal.db"}:
        path = source if changed == "source" else legacy / changed
        with sqlite3.connect(path) as db:
            db.execute("UPDATE message SET content='Changed sentinel'")
    elif changed == "settings":
        values = json.loads(settings.read_text())
        values["student_base_rules"] = "Changed rules"
        settings.write_text(json.dumps(values))
    else:
        path = legacy / changed
        values = json.loads(path.read_text())
        if changed == "report.json":
            values["scenario_count"] = 99
        else:
            values = {}
        path.write_text(json.dumps(values))
    sealed = {p.name: p.read_bytes() for p in latest.iterdir()}
    with pytest.raises(
        (ValueError, KeyError), match="mismatch|changed|artifacts"
    ):
        await rehearse(
            source,
            latest,
            apply=True,
            settings=settings,
            legacy_workspace=legacy,
        )
    assert all(
        (latest / name).read_bytes() == value for name, value in sealed.items()
    )


@pytest.mark.parametrize("legacy_input", [30], indirect=True)
@pytest.mark.parametrize("changed", ["conflict", "manifest"])
async def test_legacy_dry_run_rejects_conversion_conflict_and_stale_manifest(
    legacy_input, tmp_path, changed
):
    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    await rehearse(source, latest, settings=settings, legacy_workspace=legacy)
    if changed == "manifest":
        (legacy / "manifest.json").write_text("{}")
    else:
        config = json.loads((legacy / "manifest.json").read_text())[
            "scenarios"
        ][0]["target"]["config"]
        config["problem"]["public_text"] = "Edited by administrator"
        with sqlite3.connect(legacy / "rehearsal.db") as db:
            db.execute(
                "UPDATE scenario SET config_json=?,config_version=3",
                (json.dumps(config),),
            )
    with pytest.raises(
        ValueError,
        match="conversion_conflict|manifest_mismatch|artifact_mismatch",
    ):
        await rehearse(
            source,
            latest,
            apply=True,
            settings=settings,
            legacy_workspace=legacy,
        )
    assert not (latest / "candidate.db").exists()
    assert not (legacy / "receipt.json").exists()


async def test_old_legacy_backup_restore_loses_new_writes_without_changing_evidence(
    legacy_input, tmp_path
):
    from src.db.s2_verification import consistent_backup
    from src.db.s6_verification import restore

    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    report = await rehearse(
        source, latest, apply=True, settings=settings, legacy_workspace=legacy
    )
    written = tmp_path / "written.db"
    consistent_backup(latest / "candidate.db", written)
    with sqlite3.connect(written) as db:
        db.execute(
            "INSERT INTO message(id,session_id,role,content,created_at) "
            "VALUES(99,1,'teacher','New write sentinel','2026-01-03')"
        )
    preserved_new = tmp_path / "before-repair.db"
    restore(written, preserved_new)
    restored_old = tmp_path / "restored-old.db"
    restore(legacy / "backup.db", restored_old)
    with readonly(preserved_new) as db:
        assert db.execute(
            "SELECT content FROM message WHERE id=99"
        ).fetchone() == ("New write sentinel",)
    with readonly(restored_old) as db, readonly(source) as before:
        assert (
            db.execute("SELECT content FROM message WHERE id=99").fetchone()
            is None
        )
        assert database_state(db) == database_state(before)
    assert (
        await rehearse(
            source,
            latest,
            apply=True,
            settings=settings,
            legacy_workspace=legacy,
        )
        == report
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        "missing_settings",
        "missing_workspace",
        "overlap",
        "settings_symlink",
        "settings_hardlink",
    ],
)
async def test_legacy_arguments_and_paths_are_refused(
    legacy_input, tmp_path, unsafe
):
    import os

    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    arguments = dict(settings=settings, legacy_workspace=legacy)
    if unsafe == "missing_settings":
        arguments.pop("settings")
    elif unsafe == "missing_workspace":
        arguments.pop("legacy_workspace")
    elif unsafe == "overlap":
        arguments["legacy_workspace"] = latest
    elif unsafe == "settings_symlink":
        alias = tmp_path / "settings-alias.json"
        alias.symlink_to(settings)
        arguments["settings"] = alias
    else:
        os.link(settings, tmp_path / "settings-link.json")
    with pytest.raises(
        ValueError,
        match="legacy_arguments_required|isolated_workspace_required|private_artifacts_required",
    ):
        await rehearse(source, latest, apply=True, **arguments)
    assert not (latest / "candidate.db").exists()
    assert not (legacy / "backup.db").exists()


async def test_legacy_cli_entry_reports_ready_without_a_subprocess(
    legacy_input, tmp_path, monkeypatch, capsys
):
    import asyncio

    from src.db.s6_cutover import main

    source, settings = legacy_input
    monkeypatch.setattr(
        "sys.argv",
        [
            "s6_cutover",
            "--source-copy",
            str(source),
            "--settings",
            str(settings),
            "--legacy-workspace",
            str(tmp_path / "s2"),
            "--workspace",
            str(tmp_path / "s6"),
            "--apply",
        ],
    )
    await asyncio.to_thread(main)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "ready" and report["release_blocked"] is True
    assert report["legacy"]["source_revision"] == 23
    assert report["candidate_revision"] == 36
    assert "Original {text}" not in output.out + output.err


@pytest.mark.parametrize("missing", ["report.json", "backup.db"])
async def test_completed_latest_evidence_refuses_missing_s2_artifacts_without_recreation(
    legacy_input, tmp_path, missing
):
    source, settings = legacy_input
    legacy, latest = tmp_path / "s2", tmp_path / "s6"
    await rehearse(
        source, latest, apply=True, settings=settings, legacy_workspace=legacy
    )
    (legacy / missing).unlink()
    with pytest.raises(ValueError, match="incomplete_evidence"):
        await rehearse(
            source,
            latest,
            apply=True,
            settings=settings,
            legacy_workspace=legacy,
        )
    assert not (legacy / missing).exists()

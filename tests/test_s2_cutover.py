"""Offline S2 rehearsal on a WAL copy; never operating databases or keys."""

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_drafts import post
from test_scenario_publication import api, draft, publishable

from src.db.migrations import migrate

client = client_fixture
__all__ = ["api", "draft", "publishable"]


@pytest.fixture
async def source(data, monkeypatch):
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate.run_all_migrations(through=30)
    path = Path(data.engine.url.database)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute(
            "UPDATE scenario SET video_transcript='private original', "
            "problem_situation='Public problem'"
        )
        db.execute(
            "INSERT INTO message(id,session_id,role,content,created_at) "
            "VALUES(40,1,'teacher','Original {text}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO session_summary(id,session_id,distribution_json,feedback) "
            "VALUES(41,1,'{}','Original feedback')"
        )
        db.execute(
            "INSERT INTO question_analysis(id,message_id,label,grade,confidence,meta_json) "
            "VALUES(42,40,'Original label','우수',0.9,'{\"reasoning\":\"Original\"}')"
        )
        db.execute(
            "INSERT INTO session_feedback_report(id,session_id,version,model,prompt_hash,status,payload_json,created_at) "
            "VALUES(43,1,1,'old-model','old-hash','ok','{\"brief_feedback\":[\"Original feedback\"]}','2026-01-02')"
        )
        db.execute(
            "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at,finished_at) "
            "VALUES('original-run',1,1,'old-turn','student','old-request','input','config','openai','old-model','failed','2026-01-01','2026-01-01')"
        )
        db.execute(
            "INSERT INTO api_usage_log(id,session_id,run_id,bot_type,model,total_tokens,estimated_cost_usd,timestamp,status) "
            "VALUES(44,1,'original-run','student','old-model',12,0.012345,'2026-01-01','failed')"
        )
        db.commit()
        assert Path(str(path) + "-wal").stat().st_size > 0
        yield path


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_default_rehearsal_backs_up_wal_and_does_not_convert_source(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    report = await rehearse(
        source, workspace, Path("tests/fixtures/s2_legacy_effective.json")
    )
    assert report["status"] == "dry_run"
    assert report["scenario_count"] == 1
    archive = json.loads((workspace / "archive.json").read_text())
    assert archive["scenarios"][0]["video_transcript"] == "private original"
    assert workspace.stat().st_mode & 0o777 == 0o700
    for name in ("backup.db", "archive.json", "manifest.json"):
        assert (workspace / name).stat().st_mode & 0o777 == 0o600
    for path in (source, workspace / "backup.db", workspace / "rehearsal.db"):
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT content FROM message").fetchone() == (
                "Original {text}",
            )
            assert db.execute(
                "SELECT config_json FROM scenario"
            ).fetchone() == (None,)


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_apply_verifies_preservation_contracts_schema_and_restores_backup(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    settings = Path("tests/fixtures/s2_legacy_effective.json")
    report = await rehearse(source, workspace, settings, apply=True)
    assert report["status"] == "ready"
    assert report["restore_verified"] is True
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        columns = {row[1] for row in db.execute("PRAGMA table_info(scenario)")}
        assert "config_json" in columns
        assert not {"prompt", "framework_id", "video_transcript"} & columns
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('prompt_template','analysis_framework')"
            ).fetchall()
            == []
        )
        assert db.execute("SELECT content FROM message").fetchone() == (
            "Original {text}",
        )
        assert db.execute(
            "SELECT feedback FROM session_summary"
        ).fetchone() == ("Original feedback",)
        assert db.execute(
            "SELECT snapshot_origin,source_scenario_version FROM session"
        ).fetchone() == ("legacy_reconstructed", None)
        assert db.execute(
            "SELECT status,review_required FROM scenario"
        ).fetchone() == (
            "draft",
            1,
        )
        assert db.execute(
            "SELECT filename FROM _migrations ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("031_scenario_contract.sql",)
    assert await rehearse(source, workspace, settings, apply=True) == report
    with sqlite3.connect(source) as db:
        assert db.execute(
            "SELECT video_transcript FROM scenario"
        ).fetchone() == ("private original",)
    with sqlite3.connect(workspace / "restore.db") as db:
        assert db.execute("SELECT config_json FROM scenario").fetchone() == (
            None,
        )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_final_runtime_reads_history_and_refuses_legacy_writes(
    data, source, tmp_path, client
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{workspace / 'rehearsal.db'}"
    )
    data.factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        login(client, data.owner)
        history = await client.get("/sessions/1")
        assert history.status_code == 200, history.text
        assert "읽기 전용" in history.text
        assert "Original {text}" in history.text
        assert "private original" not in history.text
        exported = await client.get("/sessions/1/export.csv")
        assert exported.status_code == 200
        assert "Original feedback" in exported.text
        for operation in ("analyze", "end"):
            response = await client.post(f"/sessions/1/{operation}")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "legacy_read_only"
        response = await client.post("/sessions", json={"scenario_id": 1})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "scenario_not_published"
        login(client, data.other)
        assert (await client.get("/sessions/1/export.csv")).status_code == 403
        login(client, data.admin)
        assert (await client.get("/admin/scenarios/1")).status_code == 200
    finally:
        await engine.dispose()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_interruption_after_contract_resumes_without_reconversion(
    source, tmp_path, monkeypatch
):
    import os

    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    settings = Path("tests/fixtures/s2_legacy_effective.json")
    original = os.open

    def interrupt(path, flags, *args, **kwargs):
        if Path(path).name == "report.json":
            raise OSError("simulated process interruption")
        return original(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", interrupt)
        with pytest.raises(OSError, match="interruption"):
            await rehearse(source, workspace, settings, apply=True)
    assert not (workspace / "report.json").exists()
    resumed = await rehearse(source, workspace, settings, apply=True)
    assert resumed["status"] == "ready"
    assert resumed["restore_verified"] is True


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_ready_rehearsal_refuses_tampered_preservation_artifact(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    settings = Path("tests/fixtures/s2_legacy_effective.json")
    await rehearse(source, workspace, settings, apply=True)
    archive = workspace / "archive.json"
    archive.write_text("{}")
    with pytest.raises(ValueError, match="artifact_mismatch"):
        await rehearse(source, workspace, settings, apply=True)


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize("active", ["session", "run"])
async def test_cutover_refuses_active_sessions_and_unfinished_runs(
    source, tmp_path, active
):
    from src.db.s2_cutover import rehearse

    with sqlite3.connect(source) as db:
        if active == "session":
            db.execute("UPDATE session SET ended_at=NULL")
        else:
            db.execute(
                "UPDATE generation_run SET status='running',finished_at=NULL"
            )
        db.commit()
    workspace = tmp_path / "private"
    with pytest.raises(ValueError, match="active_sessions|unfinished_runs"):
        await rehearse(
            source,
            workspace,
            Path("tests/fixtures/s2_legacy_effective.json"),
            apply=True,
        )
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        assert db.execute("SELECT config_json FROM scenario").fetchone() == (
            None,
        )
        assert (
            db.execute(
                "SELECT 1 FROM _migrations WHERE filename='031_scenario_contract.sql'"
            ).fetchall()
            == []
        )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_contract_without_preservation_receipt_rolls_back(source, data):
    with pytest.raises(ValueError, match="validated_cutover_required"):
        await migrate.run_migration(
            migrate.DIRECTORY / "031_scenario_contract.sql",
            db_engine=data.engine,
        )
    with sqlite3.connect(source) as db:
        assert db.execute(
            "SELECT video_transcript FROM scenario"
        ).fetchone() == ("private original",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.execute(
                "SELECT 1 FROM _migrations WHERE filename='031_scenario_contract.sql'"
            ).fetchall()
            == []
        )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_dry_run_conflict_preserves_edited_config_and_historical_mismatch(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    settings = Path("tests/fixtures/s2_legacy_effective.json")
    workspace = tmp_path / "private"
    await rehearse(source, workspace, settings)
    candidate = json.loads((workspace / "manifest.json").read_text())[
        "scenarios"
    ][0]["target"]["config"]
    candidate["problem"]["public_text"] = "Administrator edited this"
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        db.execute(
            "UPDATE scenario SET config_json=?,config_version=3",
            (json.dumps(candidate),),
        )
        db.commit()
    with pytest.raises(ValueError, match="conversion_conflict"):
        await rehearse(source, workspace, settings, apply=True)
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        assert (
            json.loads(
                db.execute("SELECT config_json FROM scenario").fetchone()[0]
            )["problem"]["public_text"]
            == "Administrator edited this"
        )
        assert "prompt" in {
            r[1] for r in db.execute("PRAGMA table_info(scenario)")
        }
    other = tmp_path / "mismatch"
    await rehearse(source, other, settings)
    with sqlite3.connect(other / "rehearsal.db") as db:
        db.execute("UPDATE session_summary SET feedback='Corrupted result'")
        db.commit()
    with pytest.raises(ValueError, match="preservation_mismatch"):
        await rehearse(source, other, settings, apply=True)
    assert not (other / "report.json").exists()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_final_runtime_starts_only_reviewed_published_native_snapshot(
    data, api, publishable, source, tmp_path
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{workspace / 'rehearsal.db'}"
    )
    data.factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        login(api, data.admin)
        converted = (await api.get("/admin/scenarios/1")).json()
        assert converted["status"] == "draft" and converted["review_required"]
        created = await post(api, "/admin/scenarios", publishable)
        assert created.status_code == 201, created.text
        scenario_id = created.json()["id"]
        login(api, data.owner)
        await api.get("/scenarios")
        started = await post(api, "/sessions", {"scenario_id": scenario_id})
        assert started.status_code == 201, started.text
        with sqlite3.connect(workspace / "rehearsal.db") as db:
            snapshot = db.execute(
                "SELECT snapshot_origin,source_scenario_version,config_snapshot_json FROM session WHERE id=?",
                (started.json()["id"],),
            ).fetchone()
            assert snapshot[:2] == ("native", 1)
            assert json.loads(snapshot[2])["config"] == publishable["config"]
        login(api, data.admin)
        await api.get("/admin/scenarios")
        publishable.update(expected_version=1, action="save_draft")
        publishable["config"]["student"][
            "behavior_instruction"
        ] = "Changed later"
        edited = await post(
            api, f"/admin/scenarios/{scenario_id}/update", publishable
        )
        assert edited.status_code == 200, edited.text
        login(api, data.owner)
        await api.get("/scenarios")
        assert (
            await post(api, "/sessions", {"scenario_id": scenario_id})
        ).status_code == 400
        with sqlite3.connect(workspace / "rehearsal.db") as db:
            assert db.execute(
                "SELECT config_snapshot_json FROM session WHERE id=?",
                (started.json()["id"],),
            ).fetchone() == (snapshot[2],)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_contract_interruption_rolls_back_cleanup_and_retries(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    workspace = tmp_path / "private"
    settings = Path("tests/fixtures/s2_legacy_effective.json")
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    def interrupt(conn, cursor, statement, parameters, context, many):
        if (
            statement.startswith("INSERT INTO _migrations")
            and "031_scenario_contract.sql" in parameters
        ):
            raise RuntimeError("simulated migration interruption")

    event.listen(Engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            await rehearse(source, workspace, settings, apply=True)
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt)
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        assert "prompt" in {
            row[1] for row in db.execute("PRAGMA table_info(scenario)")
        }
        assert (
            db.execute(
                "SELECT 1 FROM _migrations WHERE filename='031_scenario_contract.sql'"
            ).fetchall()
            == []
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (await rehearse(source, workspace, settings, apply=True))[
        "status"
    ] == "ready"


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_cli_refusal_is_actionable_and_keeps_unrelated_files(
    source, tmp_path
):
    import os
    import subprocess
    import sys

    unrelated = tmp_path / "unrelated.db"
    unrelated.write_bytes(b"unrelated database sentinel")
    environment = tmp_path / ".env"
    environment.write_text("SECRET=never touched")
    with sqlite3.connect(source) as db:
        db.execute("UPDATE session SET ended_at=NULL")
        db.commit()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.db.s2_cutover",
            "--source-copy",
            str(source),
            "--workspace",
            str(tmp_path / "private"),
            "--settings",
            "tests/fixtures/s2_legacy_effective.json",
            "--apply",
        ],
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "active_sessions" in result.stderr
    assert "private original" not in result.stderr + result.stdout
    assert unrelated.read_bytes() == b"unrelated database sentinel"
    assert environment.read_text() == "SECRET=never touched"


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_contract_archives_unreferenced_sources_and_matches_fresh_schema(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    with sqlite3.connect(source) as db:
        db.execute(
            "INSERT INTO prompt_template(id,bot_type,template_name,template_text,version,created_at,updated_at) "
            "VALUES(90,'student','Orphan','Unreferenced original {text}',2,'2026-01-01','2026-01-02')"
        )
        db.execute(
            "INSERT INTO analysis_framework(id,name,labels_json,created_at) "
            "VALUES(91,'Orphan framework','[\"Original\",\"Labels\"]','2026-01-01')"
        )
        db.commit()
    workspace = tmp_path / "private"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    archive = json.loads((workspace / "archive.json").read_text())
    assert (
        archive["prompt_templates"][0]["template_text"]
        == "Unreferenced original {text}"
    )
    assert archive["analysis_frameworks"][-1]["id"] == 91
    upgraded = create_async_engine(
        f"sqlite+aiosqlite:///{workspace / 'rehearsal.db'}"
    )
    try:
        await migrate.run_all_migrations(db_engine=upgraded)
    finally:
        await upgraded.dispose()
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        await migrate.run_all_migrations(db_engine=fresh)
        await migrate.run_all_migrations(db_engine=fresh)
        sql = "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name!='_migrations' ORDER BY type,name"
        async with fresh.connect() as conn:
            expected = (await conn.exec_driver_sql(sql)).all()
        with sqlite3.connect(workspace / "rehearsal.db") as db:
            assert db.execute(sql).fetchall() == expected
    finally:
        await fresh.dispose()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_finished_usage_attempt_is_preserved_without_blocking_cutover(
    source, tmp_path
):
    from src.db.s2_cutover import rehearse

    with sqlite3.connect(source) as db:
        db.execute(
            "UPDATE api_usage_log SET status='running',started_at='2026-01-01',finished_at='2026-01-02'"
        )
        db.commit()
    workspace = tmp_path / "private"
    assert (
        await rehearse(
            source,
            workspace,
            Path("tests/fixtures/s2_legacy_effective.json"),
            apply=True,
        )
    )["status"] == "ready"
    with sqlite3.connect(workspace / "rehearsal.db") as db:
        assert db.execute(
            "SELECT status,finished_at FROM api_usage_log"
        ).fetchone() == ("running", "2026-01-02")

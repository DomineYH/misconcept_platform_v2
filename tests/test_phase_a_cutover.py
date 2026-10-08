"""Rehearse 023 → 024 and WAL-safe rollback using synthetic data only."""

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_scenario_api import assert_no_video, login
from test_scenario_api import client as client_fixture

from src.db.migrations import migrate

client = client_fixture


def snapshot(db):
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
        for (name,) in tables
    }


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_wal_backup_upgrade_readers_and_restore(
    data, client, tmp_path, monkeypatch
):
    source_path = Path(data.engine.url.database)
    backup_path, copy_path = tmp_path / "backup.db", tmp_path / "upgrade.db"
    copy_engine = create_async_engine(f"sqlite+aiosqlite:///{copy_path}")
    try:
        # Keep this connection open: the committed fixture must remain in WAL.
        with sqlite3.connect(source_path) as source:
            assert source.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            source.execute("PRAGMA wal_autocheckpoint=0")
            source.executescript(
                "CREATE TABLE _migrations (id INTEGER PRIMARY KEY, "
                "filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMP "
                "DEFAULT CURRENT_TIMESTAMP);"
                "INSERT INTO _migrations(filename) "
                "VALUES ('023_schema_baseline');"
                "UPDATE scenario SET problem_situation='Public problem', "
                "video_url='https://example.com/legacy-secret', "
                "video_transcript='PRIVATE LEGACY TRANSCRIPT';"
                "INSERT INTO message VALUES "
                "(41,1,'teacher','=Legacy question',"
                "'{\"analysis\":\"preserved\"}','2026-01-01 00:00:01'),"
                "(42,1,'student','Legacy answer',"
                "'{\"misconception\":true}','2026-01-01 00:00:02'),"
                "(43,1,'tutor','Legacy coaching',NULL,'2026-01-01 00:00:03');"
                "INSERT INTO question_analysis VALUES "
                "(7,41,'A','우수',0.9,'{\"reasoning\":\"old\"}');"
                "INSERT INTO session_summary VALUES "
                "(8,1,'{\"A\":1}','Preserved feedback','2026-01-02');"
                "INSERT INTO session_feedback_report VALUES "
                "(9,1,1,'old-model','old-hash','ok',"
                '\'{"brief_feedback":["Preserved feedback"]}\','
                "'2026-01-02');"
            )
            before = snapshot(source)
            old_schema = source.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            assert Path(f"{source_path}-wal").stat().st_size > 0
            with sqlite3.connect(backup_path) as backup:
                source.backup(backup)
                assert snapshot(backup) == before
                with sqlite3.connect(copy_path) as copy:
                    backup.backup(copy)

            monkeypatch.setattr(migrate, "engine", copy_engine)
            await migrate.run_all_migrations()
            await migrate.run_all_migrations()
            with sqlite3.connect(copy_path) as copy:
                after = snapshot(copy)
                for table, rows in before.items():
                    if table not in {"message", "_migrations"}:
                        assert after[table] == rows
                assert [row[:6] for row in after["message"]] == before[
                    "message"
                ]
                assert all(
                    row[6:] == (None, None, None) for row in after["message"]
                )
                assert after["generation_run"] == []
                assert len(after["_migrations"]) == 2
                assert copy.execute("PRAGMA integrity_check").fetchone() == (
                    "ok",
                )
                assert copy.execute("PRAGMA foreign_key_check").fetchall() == []

            data.factory = async_sessionmaker(
                copy_engine, expire_on_commit=False, autoflush=False
            )
            login(client, data.owner)
            analysis = await client.get("/sessions/1/analysis")
            assert analysis.status_code == 200
            assert analysis.json()["feedback_status"] == "ok"
            assert analysis.json()["feedback"] == "Preserved feedback"
            assert analysis.json()["distribution"] == {"A": 1}
            reused = await client.post("/sessions/1/analyze")
            assert reused.json()["feedback_status"] == "ok"
            updates = await client.get("/sessions/1/messages/updates")
            assert [
                updates.text.index(f'data-message-id="{identity}"')
                for identity in (41, 42, 43)
            ] == sorted(
                updates.text.index(f'data-message-id="{identity}"')
                for identity in (41, 42, 43)
            )
            assert "data-turn-id=" not in updates.text
            exported = await client.get("/sessions/1/export.csv")
            rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
            assert rows[0] == [
                "session_id",
                "scenario_title",
                "student_hash",
                "timestamp",
                "role",
                "content",
                "label",
                "confidence",
                "feedback",
            ]
            assert [row[4] for row in rows[1:]] == [
                "teacher",
                "student",
                "tutor",
                "summary",
            ]
            assert rows[1][5:8] == ["'=Legacy question", "A", "0.90"]
            assert rows[-1][-1] == "Preserved feedback"
            assert data.owner.username not in exported.text
            login(client, data.other)
            for path in (
                "/sessions/1/analysis",
                "/sessions/1/export.csv",
                "/sessions/1/messages/updates",
                "/scenarios/1",
            ):
                assert (await client.get(path)).status_code == 403
            login(client, data.owner)
            assert (
                await client.get("/admin/sessions/1/download")
            ).status_code == 403
            login(client, data.admin)
            for path in ("/admin/scenarios", "/scenarios"):
                screen = await client.get(path)
                assert screen.status_code == 200
                assert_no_video(screen.text)
            modal = await client.get("/admin/sessions/1/analysis_modal")
            assert modal.status_code == 200
            assert "Preserved feedback" in modal.text
            assert "coach-msg__turn" not in modal.text
            admin_export = await client.get("/admin/sessions/1/download")
            rows = list(
                csv.DictReader(io.StringIO(admin_export.text.lstrip("\ufeff")))
            )
            assert list(rows[0]) == [
                "session_id",
                "scenario_id",
                "scenario_title",
                "teacher_id",
                "teacher_username",
                "teacher_nickname",
                "session_started_at",
                "session_ended_at",
                "message_id",
                "message_created_at",
                "role",
                "content",
                "label",
                "confidence",
                "meta_json",
                "feedback",
            ]
            assert [row["message_id"] for row in rows[:-1]] == [
                "41",
                "42",
                "43",
            ]
            assert rows[0]["meta_json"] == '{"reasoning":"old"}'

            await copy_engine.dispose()
            with sqlite3.connect(copy_path) as copy:
                copy.execute(
                    "UPDATE scenario SET title='New write lost on restore'"
                )
                copy.commit()
                assert copy.execute(
                    "SELECT title FROM scenario"
                ).fetchone() == ("New write lost on restore",)
                with sqlite3.connect(backup_path) as backup:
                    backup.backup(copy)
                assert snapshot(copy) == before
                assert (
                    copy.execute(
                        "SELECT type,name,sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    ).fetchall()
                    == old_schema
                )
                assert copy.execute("PRAGMA integrity_check").fetchone() == (
                    "ok",
                )
                assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
            assert snapshot(source) == before
    finally:
        await copy_engine.dispose()

"""S2-final WAL backup/restore, additive S3 upgrade and historical readers."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_migrations import schema
from test_s2_cutover import source
from test_scenario_api import client, login

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.db.s2_cutover import rehearse

__all__ = ["source", "client"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_s2_final_wal_restore_s3_upgrade_preserves_history_and_access(
    data, source, tmp_path, client
):
    workspace = tmp_path / "s2"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    s2_path = workspace / "rehearsal.db"
    backup_path, restore_path = tmp_path / "backup.db", tmp_path / "restore.db"
    with sqlite3.connect(s2_path) as s2:
        assert s2.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        s2.execute("PRAGMA wal_autocheckpoint=0")
        s2.execute(
            "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at,finished_at,mentor_trigger) "
            "VALUES('historical-mentor',1,1,'old-target','mentor','old-mentor-request','old-input','old-config','openai','old-model','completed','no_intervention','2026-01-01','2026-01-01','auto')"
        )
        s2.execute(
            "INSERT INTO api_usage_log(id,session_id,run_id,operation,status,timestamp,total_tokens,estimated_cost_usd,raw_usage_json) "
            "VALUES(45,1,'historical-mentor','mentor_judgment','completed','2026-01-01',17,0.123456,'{\"original\":17}'),"
            "(46,1,'historical-mentor','mentor','failed','2026-01-01',NULL,NULL,NULL)"
        )
        s2.commit()
        assert Path(str(s2_path) + "-wal").stat().st_size > 0
        # Every original column, including credentials, ACLs and unknown usage.
        before = {
            table: (
                [r[1] for r in s2.execute(f'PRAGMA table_info("{table}")')],
                s2.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                ).fetchall(),
            )
            for (table,) in s2.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        original_schema = s2.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        with sqlite3.connect(backup_path) as backup:
            s2.backup(backup)
            with sqlite3.connect(restore_path) as restored:
                backup.backup(restored)
                assert (
                    restored.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    ).fetchall()
                    == original_schema
                )
                for table, (_, rows) in before.items():
                    assert (
                        restored.execute(
                            f'SELECT * FROM "{table}" ORDER BY rowid'
                        ).fetchall()
                        == rows
                    )

    engines = [
        create_async_engine(f"sqlite+aiosqlite:///{path}")
        for path in (restore_path, tmp_path / "fresh.db")
    ]
    for engine in engines:
        event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    upgraded, fresh = engines
    data.factory = async_sessionmaker(upgraded, expire_on_commit=False)
    try:
        for _ in range(2):
            for filename in (
                "032_mentor_reason_summary.sql",
                "033_context_budget.sql",
            ):
                await migrate.run_migration(
                    migrate.DIRECTORY / filename, db_engine=upgraded
                )
        async with upgraded.connect() as db:
            for table, (columns, rows) in before.items():
                projection = ",".join(f'"{c}"' for c in columns)
                actual = (
                    await db.exec_driver_sql(
                        f'SELECT {projection} FROM "{table}" ORDER BY rowid'
                    )
                ).all()
                assert (
                    actual[: len(rows)] if table == "_migrations" else actual
                ) == rows
            for table, field in (
                ("generation_run", "mentor_reason_summary"),
                ("api_usage_log", "context_budget_json"),
            ):
                columns = (
                    await db.exec_driver_sql(f'PRAGMA table_info("{table}")')
                ).all()
                assert columns[-1][1:5] == (field, "TEXT", 0, None)
                assert set(
                    (
                        await db.exec_driver_sql(
                            f'SELECT "{field}" FROM "{table}"'
                        )
                    ).scalars()
                ) == {None}
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            assert (
                await db.exec_driver_sql(
                    "SELECT filename,count(*) FROM _migrations WHERE filename IN ('032_mentor_reason_summary.sql','033_context_budget.sql') GROUP BY filename ORDER BY filename"
                )
            ).all() == [
                ("032_mentor_reason_summary.sql", 1),
                ("033_context_budget.sql", 1),
            ]
            upgraded_schema = await schema(db)
        for _ in range(2):
            await migrate.run_all_migrations(through=33, db_engine=fresh)
        async with fresh.connect() as db:
            assert await schema(db) == upgraded_schema

        # Current result readers require the later additive analysis migration.
        await migrate.run_migration(
            migrate.DIRECTORY / "034_analysis_run.sql", db_engine=upgraded
        )
        login(client, data.owner)
        history = await client.get("/sessions/1")
        exported = await client.get("/sessions/1/export.csv")
        assert history.status_code == exported.status_code == 200
        assert "읽기 전용" in history.text
        assert 'data-result-url="/sessions/1/analysis"' in history.text
        result = await client.get("/sessions/1/analysis")
        assert result.status_code == 200
        assert [m["content"] for m in result.json()["messages"]] == [
            "Original {text}"
        ]
        assert (
            "Original feedback" in exported.text
            and "Original label" in exported.text
        )
        for operation in ("analyze", "end"):
            blocked = await client.post(f"/sessions/1/{operation}")
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["code"] == "legacy_read_only"
        login(client, data.other)
        for path in ("/sessions/1", "/sessions/1/export.csv"):
            assert (await client.get(path)).status_code == 403
        login(client, data.admin)
        assert (await client.get("/sessions/1/export.csv")).status_code == 403
        admin_export = await client.get("/admin/sessions/export")
        assert admin_export.status_code == 200
        assert (
            "Original feedback" in admin_export.text
            and "Original label" in admin_export.text
        )
        with sqlite3.connect(s2_path) as s2:
            assert (
                s2.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                == original_schema
            )
            for table, (_, rows) in before.items():
                assert (
                    s2.execute(
                        f'SELECT * FROM "{table}" ORDER BY rowid'
                    ).fetchall()
                    == rows
                )
    finally:
        for engine in engines:
            await engine.dispose()

"""S4 to S5 on temporary WAL backups, including the restore write boundary."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import defer
from test_s2_cutover import source
from test_s4_cutover import digest

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.db.s2_cutover import rehearse
from src.models import GenerationRun
from src.services.export import CSVExporter

__all__ = ["source"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def snapshot(db):
    return {
        table: (
            [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')],
            db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall(),
        )
        for (table,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


async def exports(engine, *, s4=False):
    exporter = CSVExporter()
    async with async_sessionmaker(engine)() as db:
        if s4:
            # Current CSV code reads only S4 run fields; omit absent S5 columns.
            @event.listens_for(db.sync_session, "do_orm_execute")
            def s4_columns(query):
                if query.is_select and any(
                    item["entity"] is GenerationRun
                    for item in query.statement.column_descriptions
                ):
                    query.statement = query.statement.options(
                        defer(GenerationRun.comparison_run_id),
                        defer(GenerationRun.panel_id),
                    )

        return [
            await exporter.export_session(1, db),
            await exporter.export_session_admin(1, db),
            await exporter.export_multiple_sessions([1], db),
            await exporter.export_multiple_sessions_admin([1], db),
        ]


async def test_s5_wal_migration_restore_preserves_records_csv_and_rollback_boundary(
    data, source, tmp_path
):
    workspace = tmp_path / "s4"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    source_path = workspace / "rehearsal.db"
    original_engine = create_async_engine(f"sqlite+aiosqlite:///{source_path}")
    await migrate.run_all_migrations(through=34, db_engine=original_engine)
    await original_engine.dispose()
    candidate_path, backup_path = (
        tmp_path / "candidate.db",
        tmp_path / "backup.db",
    )
    with sqlite3.connect(source_path) as original:
        original.execute("PRAGMA foreign_keys=ON")
        assert original.execute("PRAGMA journal_mode=WAL").fetchone() == (
            "wal",
        )
        original.execute("PRAGMA wal_autocheckpoint=0")
        original.execute(
            "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at,plan_json,outcome_json,accepted_report_id,accepted_report_version) "
            "VALUES('s4-analysis',1,1,'analysis-turn','analysis','s4-request','input-hash','config-hash','openai','old-model','ok','2026-01-01','{\"chunks\":1}','{\"status\":\"ok\"}',43,1)"
        )
        original.execute(
            "INSERT INTO api_usage_log(id,session_id,run_id,operation,status,timestamp,total_tokens,estimated_cost_usd) "
            "VALUES(51,1,'s4-analysis','analysis_unified','completed','2026-01-01',17,0.123456),"
            "(52,1,'s4-analysis','analysis_unified','failed','2026-01-01',NULL,NULL)"
        )
        original.commit()
        assert Path(str(source_path) + "-wal").stat().st_size > 0
        before = snapshot(original)
        original_schema = original.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        with sqlite3.connect(backup_path) as backup:
            original.backup(backup)
            assert snapshot(backup) == before
            with sqlite3.connect(candidate_path) as restored:
                backup.backup(restored)
                assert snapshot(restored) == before

    engine = create_async_engine(f"sqlite+aiosqlite:///{candidate_path}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        csv_before = await exports(engine, s4=True)
        assert "Original feedback" in csv_before[0]
        assert "Original {text}" in csv_before[0]
        for _ in range(2):
            await migrate.run_all_migrations(through=35, db_engine=engine)
        assert await exports(engine) == csv_before
        with sqlite3.connect(candidate_path) as candidate:
            assert candidate.execute("PRAGMA integrity_check").fetchone() == (
                "ok",
            )
            assert (
                candidate.execute("PRAGMA foreign_key_check").fetchall() == []
            )
            for table, (columns, rows) in before.items():
                fields = ",".join(f'"{column}"' for column in columns)
                actual = candidate.execute(
                    f'SELECT {fields} FROM "{table}" ORDER BY rowid'
                ).fetchall()
                if table == "_migrations":
                    actual = actual[: len(rows)]
                assert actual == rows and digest(actual) == digest(rows)
            assert candidate.execute(
                "SELECT comparison_run_id,panel_id FROM generation_run"
            ).fetchall() == [(None, None)] * len(before["generation_run"][1])
            assert candidate.execute(
                "SELECT usage_scope FROM api_usage_log"
            ).fetchall() == [(None,)] * len(before["api_usage_log"][1])
            with sqlite3.connect(tmp_path / "restored-s5.db") as restored:
                candidate.backup(restored)
                assert snapshot(restored) == snapshot(candidate)
                assert (
                    restored.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    ).fetchall()
                    == candidate.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    ).fetchall()
                )
                assert restored.execute(
                    "PRAGMA integrity_check"
                ).fetchone() == ("ok",)
                assert (
                    restored.execute("PRAGMA foreign_key_check").fetchall()
                    == []
                )
            candidate.execute(
                "INSERT INTO comparison_run(id,admin_id,target_role,mode,source_snapshot,source_hash,canonical_context,context_hash,panel_configs,request_id,input_hash,created_at,expires_at,status) "
                "VALUES('new-s5',3,'student','same_context','{}','hash','[]','context','[]','request','input','2026-01-01','2026-01-31','finished')"
            )
            candidate.execute(
                "INSERT INTO generation_run(id,owner_id,comparison_run_id,panel_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at) "
                "VALUES('new-generation',3,'new-s5','1','turn','student','request','input','config','openai','model','completed','2026-01-01')"
            )
            candidate.execute(
                "INSERT INTO api_usage_log(id,run_id,usage_scope,timestamp,total_tokens,estimated_cost_usd,status) "
                "VALUES(53,'new-generation','comparison','2026-01-01',23,0.05,'completed')"
            )
            candidate.commit()
            current = snapshot(candidate)
            with sqlite3.connect(tmp_path / "before-repair.db") as preserved:
                candidate.backup(preserved)
                assert snapshot(preserved) == current
        assert await exports(engine) == csv_before
        await engine.dispose()
        with sqlite3.connect(backup_path) as backup:
            with sqlite3.connect(candidate_path) as restored:
                backup.backup(restored)
                assert snapshot(restored) == before
                assert (
                    restored.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    ).fetchall()
                    == original_schema
                )
                assert restored.execute(
                    "PRAGMA integrity_check"
                ).fetchone() == ("ok",)
                assert (
                    restored.execute("PRAGMA foreign_key_check").fetchall()
                    == []
                )
                assert restored.execute(
                    "SELECT count(*) FROM api_usage_log WHERE id=53"
                ).fetchone() == (0,)
                with pytest.raises(
                    sqlite3.OperationalError, match="no such table"
                ):
                    restored.execute("SELECT * FROM comparison_run")
        with sqlite3.connect(source_path) as original:
            assert snapshot(original) == before
            assert (
                original.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                == original_schema
            )
    finally:
        await engine.dispose()

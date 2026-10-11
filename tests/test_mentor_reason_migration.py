"""032 adds private nullable storage without rewriting historical evidence."""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_032_preserves_history_unknowns_links_and_constraints(data):
    await migrate.run_all_migrations(through=30, db_engine=data.engine)
    async with data.engine.begin() as db:
        await db.exec_driver_sql(
            "INSERT INTO generation_run (id,owner_id,session_id,turn_id,operation,request_id,input_hash,"
            "config_hash,provider,model,status,result_kind,started_at,mentor_trigger) VALUES "
            "('old',1,1,'turn','mentor','request','input-hash','config-hash','openai','model','completed','message','2026-01-01','auto')"
        )
        await db.exec_driver_sql(
            "INSERT INTO message (id,session_id,role,content,created_at,turn_id,turn_index,generation_run_id) "
            "VALUES (10,1,'tutor','Original coaching','2026-01-01','turn',1,'old')"
        )
        await db.exec_driver_sql(
            "INSERT INTO api_usage_log (id,session_id,run_id,timestamp,status,operation,total_tokens,estimated_cost_usd) "
            "VALUES (20,1,'old','2026-01-01','completed','mentor_judgment',NULL,NULL),"
            "(21,1,'old','2026-01-01','completed','mentor',12,0.123)"
        )
        before = {
            table: (
                await db.exec_driver_sql(f"SELECT * FROM {table} ORDER BY id")
            ).all()
            for table in (
                "generation_run",
                "message",
                "api_usage_log",
                "session",
            )
        }
        constraints = (
            await db.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='generation_run' ORDER BY name"
            )
        ).all()
    for _ in range(2):
        await migrate.run_migration(
            migrate.DIRECTORY / "032_mentor_reason_summary.sql",
            db_engine=data.engine,
        )
    async with data.engine.begin() as db:
        assert (
            await db.exec_driver_sql("SELECT * FROM generation_run ORDER BY id")
        ).all() == [tuple(before["generation_run"][0]) + (None,)]
        for table in ("message", "api_usage_log", "session"):
            assert (
                await db.exec_driver_sql(f"SELECT * FROM {table} ORDER BY id")
            ).all() == before[table]
        assert (
            await db.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='generation_run' ORDER BY name"
            )
        ).all() == constraints
        columns = (
            await db.exec_driver_sql("PRAGMA table_info(generation_run)")
        ).all()
        assert columns[-1][1:5] == ("mentor_reason_summary", "TEXT", 0, None)
        assert (
            await db.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (
            await db.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
        await db.exec_driver_sql(
            "UPDATE generation_run SET mentor_reason_summary='Private reason' WHERE id='old'"
        )
    for change in (
        "mentor_trigger='off'",
        "status='unknown'",
        "provider='unknown'",
    ):
        with pytest.raises(IntegrityError):
            async with data.engine.begin() as db:
                await db.exec_driver_sql(
                    f"UPDATE generation_run SET {change} WHERE id='old'"
                )
    with pytest.raises(IntegrityError):
        async with data.engine.begin() as db:
            await db.exec_driver_sql(
                "INSERT INTO generation_run SELECT 'duplicate',owner_id,session_id,turn_id,operation,'another',"
                "input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,"
                "first_output_at,finished_at,mentor_trigger,mentor_reason_summary FROM generation_run WHERE id='old'"
            )


async def test_032_fresh_install_and_rerun(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_all_migrations(through=32, db_engine=engine)
        await migrate.run_all_migrations(through=32, db_engine=engine)
        async with engine.connect() as db:
            columns = (
                await db.exec_driver_sql("PRAGMA table_info(generation_run)")
            ).all()
            assert columns[-1][1:5] == (
                "mentor_reason_summary",
                "TEXT",
                0,
                None,
            )
            assert (
                await db.exec_driver_sql(
                    "SELECT count(*) FROM _migrations WHERE filename='032_mentor_reason_summary.sql'"
                )
            ).scalar() == 1
    finally:
        await engine.dispose()

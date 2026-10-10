"""033 preserves 032 records and matches an idempotent fresh install."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from test_migrations import schema

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate


async def test_033_preserves_032_history_and_matches_fresh(tmp_path):
    engines = [
        create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
        for name in ("upgrade.db", "fresh.db")
    ]
    for engine in engines:
        event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    upgrade, fresh = engines
    try:
        await migrate.run_all_migrations(through=32, db_engine=upgrade)
        async with upgrade.begin() as db:
            for statement in (
                "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES (1,'old','Old','hash','admin','2026-01-01')",
                "INSERT INTO scenario(id,title,is_active,created_at,config_json) VALUES(1,'Original',1,'2026-01-01','{}')",
                "INSERT INTO session(id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count,config_snapshot_json,config_hash,snapshot_origin) VALUES(1,1,1,'2026-01-01',0,0,'{\"old\":\"snapshot\"}','original-hash','native')",
                "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at) VALUES('old',1,1,'turn','student','request','input','original-hash','openai','gpt-5-mini','completed','message','2026-01-01')",
                "INSERT INTO message(id,session_id,role,content,created_at,turn_id,turn_index,generation_run_id) VALUES(10,1,'teacher','Original question','2026-01-01','turn',1,NULL),(11,1,'student','Original answer','2026-01-01','turn',1,'old')",
                "INSERT INTO api_usage_log(id,session_id,run_id,timestamp,status,operation,total_tokens,estimated_cost_usd,raw_usage_json) VALUES(20,1,'old','2026-01-01','failed','student',NULL,NULL,NULL),(21,1,'old','2026-01-01','completed','mentor_judgment',12,0.123,'{\"tokens\":12}')",
            ):
                await db.exec_driver_sql(statement)
            before = {
                table: (
                    await db.exec_driver_sql(
                        f"SELECT * FROM {table} ORDER BY id"
                    )
                ).all()
                for table in (
                    "session",
                    "generation_run",
                    "message",
                    "api_usage_log",
                )
            }
            indexes = (
                await db.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
                )
            ).all()
        for _ in range(2):
            await migrate.run_migration(
                migrate.DIRECTORY / "033_context_budget.sql", db_engine=upgrade
            )
        async with upgrade.begin() as db:
            for table, rows in before.items():
                actual = (
                    await db.exec_driver_sql(
                        f"SELECT * FROM {table} ORDER BY id"
                    )
                ).all()
                assert actual == (
                    [tuple(row) + (None,) for row in rows]
                    if table == "api_usage_log"
                    else rows
                )
            columns = (
                await db.exec_driver_sql("PRAGMA table_info(api_usage_log)")
            ).all()
            assert columns[-1][1:5] == ("context_budget_json", "TEXT", 0, None)
            assert (
                await db.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
                )
            ).all() == indexes
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            assert (
                await db.exec_driver_sql(
                    "SELECT count(*) FROM _migrations WHERE filename='033_context_budget.sql'"
                )
            ).scalar() == 1
            upgraded_schema = await schema(db)
        await migrate.run_all_migrations(through=33, db_engine=fresh)
        await migrate.run_all_migrations(through=33, db_engine=fresh)
        async with fresh.connect() as db:
            assert await schema(db) == upgraded_schema
    finally:
        for engine in engines:
            await engine.dispose()

"""Fresh and populated upgrades preserve existing comparison/classroom history."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from test_migrations import schema

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate


async def test_036_is_additive_repeatable_and_matches_fresh_install(tmp_path):
    old = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    for engine in (old, fresh):
        event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_all_migrations(through=35, db_engine=old)
        async with old.begin() as db:
            await db.exec_driver_sql(
                "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES(1,'admin','Admin','hash','admin','2026-01-01')"
            )
            await db.exec_driver_sql(
                "INSERT INTO comparison_run(id,admin_id,target_role,mode,source_snapshot,source_hash,canonical_context,context_hash,panel_configs,request_id,input_hash,created_at,expires_at,status) VALUES('legacy',1,'student','same_context','{}','hash','[]','ctx','[]','req','hash','2026-01-01','2026-01-31','finished')"
            )
            await db.exec_driver_sql(
                "INSERT INTO generation_run(id,owner_id,comparison_run_id,panel_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at,outcome_json) VALUES('attempt',1,'legacy','a','turn','student','req','hash','hash','openai','model','completed','2026-01-01','{\"text\":\"Original\"}')"
            )
            await db.exec_driver_sql(
                "INSERT INTO api_usage_log(id,run_id,timestamp,estimated_cost_usd,usage_scope) VALUES(1,'attempt','2026-01-01',0.02,'comparison')"
            )
            columns = [
                column[1]
                for column in await db.exec_driver_sql(
                    "PRAGMA table_info(comparison_run)"
                )
            ]
            before = (
                await db.exec_driver_sql("SELECT * FROM comparison_run")
            ).all()
            ledger = (
                await db.exec_driver_sql("SELECT * FROM api_usage_log")
            ).all()
            attempts = (
                await db.exec_driver_sql("SELECT * FROM generation_run")
            ).all()
        for _ in range(2):
            await migrate.run_migration(
                migrate.DIRECTORY / "036_comparison_conversation.sql",
                db_engine=old,
            )
        async with old.connect() as db:
            assert (
                await db.exec_driver_sql(
                    "SELECT " + ",".join(columns) + " FROM comparison_run"
                )
            ).all() == before
            assert (
                await db.exec_driver_sql(
                    "SELECT conversation_id FROM comparison_run"
                )
            ).scalar() is None
            assert (
                await db.exec_driver_sql("SELECT * FROM generation_run")
            ).all() == attempts
            assert (
                await db.exec_driver_sql("SELECT * FROM api_usage_log")
            ).all() == ledger
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            upgraded_schema = await schema(db)
        await migrate.run_all_migrations(db_engine=fresh)
        async with fresh.connect() as db:
            assert await schema(db) == upgraded_schema
    finally:
        await old.dispose()
        await fresh.dispose()

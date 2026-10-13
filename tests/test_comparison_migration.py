"""Comparison ownership preserves classroom records and constraints."""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from test_migrations import schema

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate


async def test_035_preserves_records_indexes_and_foreign_keys(tmp_path):
    upgrade = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'new.db'}")
    for engine in (upgrade, fresh):
        event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_all_migrations(through=34, db_engine=upgrade)
        async with upgrade.begin() as db:
            for sql in (
                "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES(1,'admin','Admin','hash','admin','2026-01-01')",
                "INSERT INTO scenario(id,title,is_active,created_at,config_json) VALUES(1,'Old',1,'2026-01-01','{}')",
                "INSERT INTO session(id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count) VALUES(1,1,1,'2026-01-01',0,0)",
                "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at,outcome_json) VALUES('old',1,1,'turn','student','req','input','config','openai','old','completed','2026-01-01','{}')",
                "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at,plan_json,outcome_json,accepted_report_id,accepted_report_version) VALUES('old-analysis',1,1,'analysis-turn','analysis','analysis-req','input','config','google','old','ok','2026-01-01','{\"chunks\":1}','{\"status\":\"ok\"}',30,2)",
                "INSERT INTO message(id,session_id,role,content,created_at,generation_run_id) VALUES(10,1,'student','Original','2026-01-01','old')",
                "INSERT INTO api_usage_log(id,session_id,run_id,timestamp,total_tokens) VALUES(20,1,'old','2026-01-01',12)",
            ):
                await db.exec_driver_sql(sql)
            columns = {
                table: ",".join(
                    row[1]
                    for row in await db.exec_driver_sql(
                        f"PRAGMA table_info({table})"
                    )
                )
                for table in (
                    "generation_run",
                    "message",
                    "api_usage_log",
                    "session",
                )
            }
            before = {
                table: (
                    await db.exec_driver_sql(
                        f"SELECT {fields} FROM {table} ORDER BY id"
                    )
                ).all()
                for table, fields in columns.items()
            }
            indexes = (
                await db.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='generation_run' AND sql IS NOT NULL ORDER BY name"
                )
            ).all()
        for _ in range(2):
            await migrate.run_migration(
                migrate.DIRECTORY / "035_comparison_run.sql", db_engine=upgrade
            )
        async with upgrade.connect() as db:
            for table, fields in columns.items():
                assert (
                    await db.exec_driver_sql(
                        f"SELECT {fields} FROM {table} ORDER BY id"
                    )
                ).all() == before[table]
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            assert (
                await db.exec_driver_sql(
                    "SELECT usage_scope FROM api_usage_log"
                )
            ).scalar() is None
            for name, sql in indexes:
                assert (
                    await db.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name=?", (name,)
                    )
                ).scalar() == sql
            upgraded = await schema(db)
        await migrate.run_all_migrations(through=35, db_engine=fresh)
        async with fresh.connect() as db:
            assert await schema(db) == upgraded
        async with upgrade.begin() as db:
            await db.exec_driver_sql(
                "INSERT INTO comparison_run(id,admin_id,target_role,mode,source_snapshot,source_hash,canonical_context,context_hash,panel_configs,request_id,input_hash,created_at,expires_at,status) VALUES('compare',1,'student','same_context','{}','hash','[]','ctx','[]','req','hash','2026-01-01','2026-01-31','running')"
            )
            await db.exec_driver_sql(
                "INSERT INTO generation_run(id,owner_id,comparison_run_id,panel_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at) VALUES('new',1,'compare','1','turn','student','req','hash','hash','openai','model','running','2026-01-01')"
            )
        for sql in (
            "INSERT INTO comparison_run(id,admin_id,target_role,mode,source_snapshot,source_hash,canonical_context,context_hash,panel_configs,request_id,input_hash,created_at,expires_at,status) VALUES('busy',1,'student','same_context','{}','hash','[]','ctx','[]','other','hash','2026-01-01','2026-01-31','running')",
            "UPDATE generation_run SET session_id=1 WHERE id='new'",
            "UPDATE generation_run SET panel_id=NULL WHERE id='new'",
            "UPDATE generation_run SET comparison_run_id=NULL WHERE id='new'",
            "UPDATE generation_run SET panel_id='1' WHERE id='old'",
            "UPDATE generation_run SET status='ok' WHERE id='old'",
            "INSERT INTO generation_run SELECT 'duplicate',owner_id,session_id,turn_id,operation,'other',input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,first_output_at,finished_at,mentor_trigger,mentor_reason_summary,plan_json,outcome_json,accepted_report_id,accepted_report_version,comparison_run_id,panel_id FROM generation_run WHERE id='new'",
        ):
            with pytest.raises(IntegrityError):
                async with upgrade.begin() as db:
                    await db.exec_driver_sql(sql)
    finally:
        await upgrade.dispose()
        await fresh.dispose()

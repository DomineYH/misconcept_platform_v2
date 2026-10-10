"""034 preserves runs, messages, usage and accepted reports without backfill."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from test_migrations import schema

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate


async def test_034_preserves_033_records_and_matches_fresh(tmp_path):
    engines = [
        create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
        for name in ("upgrade.db", "fresh.db")
    ]
    for engine in engines:
        event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    upgrade, fresh = engines
    try:
        await migrate.run_all_migrations(through=33, db_engine=upgrade)
        async with upgrade.begin() as db:
            for statement in (
                "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES(1,'old','Old','hash','admin','2026-01-01')",
                "INSERT INTO scenario(id,title,is_active,created_at,config_json) VALUES(1,'Original',1,'2026-01-01','{}')",
                "INSERT INTO session(id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count) VALUES(1,1,1,'2026-01-01',0,0)",
                "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at,mentor_reason_summary) VALUES('old',1,1,'turn','student','request','input','config','google','test','completed','message','2026-01-01','Private reason')",
                "INSERT INTO message(id,session_id,role,content,created_at,turn_id,turn_index,generation_run_id) VALUES(10,1,'teacher','Original question','2026-01-01','turn',1,NULL),(11,1,'student','Original answer','2026-01-01','turn',1,'old')",
                "INSERT INTO api_usage_log(id,session_id,run_id,timestamp,status,operation,total_tokens,context_budget_json) VALUES(20,1,'old','2026-01-01','completed','student',12,'{}')",
                "INSERT INTO session_feedback_report(id,session_id,version,model,prompt_hash,status,payload_json,created_at) VALUES(30,1,1,'legacy','hash','ok','{\"brief_feedback\":[\"Old\"]}','2026-01-01')",
                "INSERT INTO session_summary(id,session_id,distribution_json,feedback,created_at) VALUES(40,1,'{}','Old','2026-01-01')",
            ):
                await db.exec_driver_sql(statement)
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
                    "session_feedback_report",
                    "session_summary",
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
        for _ in range(2):
            await migrate.run_migration(
                migrate.DIRECTORY / "034_analysis_run.sql", db_engine=upgrade
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
                    "SELECT plan_json,outcome_json,accepted_report_id,accepted_report_version FROM generation_run"
                )
            ).one() == (None, None, None, None)
            upgraded = await schema(db)
        await migrate.run_all_migrations(db_engine=fresh)
        await migrate.run_all_migrations(db_engine=fresh)
        async with fresh.connect() as db:
            assert await schema(db) == upgraded
    finally:
        for engine in engines:
            await engine.dispose()


async def test_analysis_constraints_keep_student_states_and_one_active_run(
    data, monkeypatch
):
    import pytest
    from analysis_fixtures import install_analysis_snapshot
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    await install_analysis_snapshot(data, monkeypatch)
    insert = text(
        "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,started_at) VALUES(:id,:actor,:session,:id,:operation,:request,'input','config','openai','model',:status,'2026-01-01')"
    )
    values = dict(
        id="analysis-a",
        actor=data.owner.id,
        session=data.session.id,
        operation="analysis",
        request="request-a",
        status="running",
    )
    with pytest.raises(IntegrityError):
        async with data.factory() as db:
            await db.execute(
                insert, {**values, "operation": "student", "status": "ok"}
            )
            await db.commit()
    async with data.factory() as db:
        await db.execute(insert, values)
        await db.commit()
    for change in (
        dict(id="analysis-b", actor=data.admin.id, request="request-b"),
        dict(id="analysis-b", status="failed"),
    ):
        with pytest.raises(IntegrityError):
            async with data.factory() as db:
                await db.execute(insert, {**values, **change})
                await db.commit()
    async with data.factory() as db:
        await db.execute(
            text("UPDATE generation_run SET status='ok' WHERE id='analysis-a'")
        )
        await db.execute(
            insert, {**values, "id": "analysis-b", "request": "request-b"}
        )
        await db.commit()

"""024 upgrades frozen schemas without inventing historical turns."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.migrations import migrate


async def test_024_preserves_legacy_and_matches_fresh(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    async with engine.begin() as conn:
        for statement in migrate.statements(
            Path("src/db/migrations/baseline.sql").read_text()
        ):
            await conn.exec_driver_sql(statement)
        await migrate._history(conn)
        await migrate._record(conn, migrate.BASELINE)
        await conn.exec_driver_sql(
            "INSERT INTO analysis_framework (id,name,labels_json,created_at) "
            "VALUES "
            "(1,'Old','[\"A\"]','2026-01-01')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO scenario (id,title,prompt,framework_id,created_at,"
            "is_active,tutor_sensitivity) VALUES "
            "(1,'Old','Old',1,'2026-01-01',1,'medium')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO session (id,scenario_id,started_at,"
            "tutor_intervention_count,tutor_question_count) VALUES "
            "(1,1,'2026-01-01',0,0)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO message VALUES "
            "(17,1,'tutor','Old coaching','{\"legacy\":true}','2026-01-01')"
        )
    await migrate.run_all_migrations(through=30)
    await migrate.run_all_migrations(through=30)
    async with engine.connect() as conn:
        assert (
            await conn.exec_driver_sql(
                "SELECT id,role,content,metadata,turn_id,turn_index,"
                "generation_run_id FROM message"
            )
        ).one() == (
            17,
            "tutor",
            "Old coaching",
            '{"legacy":true}',
            None,
            None,
            None,
        )
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (
            await conn.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
        old = (
            await conn.exec_driver_sql(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name != '_migrations' "
                "ORDER BY type,name"
            )
        ).all()
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'new.db'}")
    monkeypatch.setattr(migrate, "engine", fresh)
    await migrate.run_all_migrations(through=30)
    async with fresh.connect() as conn:
        assert (
            await conn.exec_driver_sql(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name != '_migrations' "
                "ORDER BY type,name"
            )
        ).all() == old
        assert (
            await conn.exec_driver_sql(
                "SELECT filename FROM _migrations ORDER BY id"
            )
        ).scalars().all() == [
            migrate.BASELINE,
            "024_generation_run.sql",
            "025_provider_connection.sql",
            "026_model_settings.sql",
            "027_probe_attempts.sql",
            "028_generation_providers.sql",
            "029_scenario_config.sql",
            "030_mentor_policy.sql",
        ]
    await engine.dispose()
    await fresh.dispose()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_024_constraints_indexes_and_owner_deletion(data, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate.run_all_migrations(through=30)
    insert = text(
        "INSERT INTO generation_run "
        "(id,owner_id,session_id,turn_id,operation,request_id,input_hash,"
        "config_hash,provider,model,status,started_at) VALUES "
        "(:id,:owner,:session,:turn,:operation,:request,'input','config',"
        "'openai','test',:status,'2026-01-01')"
    )
    values = dict(
        id="run-a",
        owner=data.owner.id,
        session=data.session.id,
        turn="turn-a",
        operation="student",
        request="request-a",
        status="running",
    )
    async with data.engine.begin() as conn:
        await conn.execute(insert, values)
    for change in [
        dict(id="run-b", turn="turn-b", request="request-b"),
        dict(id="run-b", status="failed"),
    ]:
        with pytest.raises(IntegrityError):
            async with data.engine.begin() as conn:
                await conn.execute(insert, {**values, **change})
    async with data.engine.begin() as conn:
        await conn.execute(
            insert,
            {
                **values,
                "id": "mentor-a",
                "operation": "mentor",
                "request": "mentor-a",
            },
        )
        await conn.exec_driver_sql(
            "UPDATE generation_run SET status='completed' WHERE id='run-a'"
        )
        await conn.exec_driver_sql(
            "INSERT INTO message (session_id,role,content,created_at,turn_id,"
            "turn_index) VALUES "
            "(1,'teacher','Question','2026-01-01','turn-a',1)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO message (session_id,role,content,created_at,turn_id,"
            "turn_index,generation_run_id) VALUES "
            "(1,'student','Answer','2026-01-01','turn-a',1,'run-a')"
        )
        plan = (
            await conn.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT turn_id FROM message WHERE "
                "session_id=1 AND role='student' AND turn_id IS NOT NULL "
                "ORDER BY turn_index DESC LIMIT 10"
            )
        ).all()
        assert any("ix_message_completed_student" in row[3] for row in plan)
        turn_plan = (
            await conn.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT * FROM message WHERE "
                "session_id=1 AND turn_id='turn-a' AND role='teacher'"
            )
        ).all()
        assert any("uq_message_turn_role" in row[3] for row in turn_plan)
    with pytest.raises(IntegrityError):
        async with data.engine.begin() as conn:
            await conn.execute(
                insert,
                {
                    **values,
                    "id": "completed-b",
                    "request": "completed-b",
                    "status": "completed",
                },
            )
    for sql in [
        "INSERT INTO message (session_id,role,content,created_at,turn_id) "
        "VALUES (1,'student','Duplicate','2026-01-01','turn-a')",
        "INSERT INTO message (session_id,role,content,created_at,turn_id,"
        "turn_index) VALUES (1,'teacher','Duplicate','2026-01-01','turn-b',1)",
        "INSERT INTO message (session_id,role,content,created_at,"
        "generation_run_id) VALUES "
        "(1,'tutor','Duplicate','2026-01-01','run-a')",
    ]:
        with pytest.raises(IntegrityError):
            async with data.engine.begin() as conn:
                await conn.exec_driver_sql(sql)
    async with data.engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM user WHERE id=:id"), {"id": data.owner.id}
        )
        assert (
            await conn.exec_driver_sql("SELECT owner_id FROM generation_run")
        ).scalars().all() == [None, None]
        assert (
            await conn.exec_driver_sql("SELECT teacher_id FROM session")
        ).scalar() is None
        assert (
            await conn.exec_driver_sql("SELECT count(*) FROM message")
        ).scalar() == 2
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []

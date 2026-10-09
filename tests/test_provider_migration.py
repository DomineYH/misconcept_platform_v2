"""025 installs connection/audit tables and preserves the current revision."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.migrations import migrate


@pytest.mark.parametrize("upgrade", [False, True])
async def test_provider_migration_fresh_upgrade_and_rerun(
    tmp_path, monkeypatch, upgrade
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    )
    monkeypatch.setattr(migrate, "engine", engine)
    try:
        if upgrade:
            await migrate._install_baseline()
            await migrate.run_migration(
                Path("src/db/migrations/024_generation_run.sql")
            )
            async with engine.begin() as conn:
                await conn.exec_driver_sql(
                    "INSERT INTO user (id,username,nickname,password_hash,role,created_at) VALUES (7,'preserved','Preserved','original-hash','admin','2026-01-01')"
                )
                await conn.exec_driver_sql(
                    "INSERT INTO analysis_framework (id,name,labels_json,created_at) VALUES (1,'Preserved','[]','2026-01-01')"
                )
                await conn.exec_driver_sql(
                    "INSERT INTO scenario (id,title,prompt,framework_id,created_at,is_active,tutor_sensitivity) VALUES (1,'Preserved','Preserved',1,'2026-01-01',1,'medium')"
                )
                await conn.exec_driver_sql(
                    "INSERT INTO session (id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count) VALUES (1,1,7,'2026-01-01',0,0)"
                )
                await conn.exec_driver_sql(
                    "INSERT INTO message (id,session_id,role,content,created_at) VALUES (9,1,'student','Stored text','2026-01-01')"
                )
        await migrate.run_all_migrations(through=30)
        await migrate.run_all_migrations(through=30)
        async with engine.connect() as conn:
            rows = (
                await conn.exec_driver_sql(
                    "SELECT provider,enabled,encrypted_key,nonce,credential_revision,connection_version FROM provider_connection ORDER BY id"
                )
            ).all()
            assert rows == [
                ("openai", 0, None, None, 0, 1),
                ("anthropic", 0, None, None, 0, 1),
                ("google", 0, None, None, 0, 1),
            ]
            assert (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM provider_audit_log"
                )
            ).scalar() == 0
            assert (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM _migrations WHERE filename='025_provider_connection.sql'"
                )
            ).scalar() == 1
            assert (
                await conn.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await conn.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            if upgrade:
                assert (
                    await conn.exec_driver_sql(
                        "SELECT username,password_hash FROM user WHERE id=7"
                    )
                ).one() == ("preserved", "original-hash")
                assert (
                    await conn.exec_driver_sql(
                        "SELECT content FROM message WHERE id=9"
                    )
                ).scalar() == "Stored text"
                assert (
                    await conn.exec_driver_sql(
                        "SELECT teacher_id FROM session WHERE id=1"
                    )
                ).scalar() == 7
    finally:
        await engine.dispose()

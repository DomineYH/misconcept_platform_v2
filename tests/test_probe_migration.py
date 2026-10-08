"""Probe/attempt installation preserves legacy usage and accepts unknown values."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.migrations import migrate


@pytest.mark.parametrize("upgrade", [False, True])
async def test_probe_attempt_schema_fresh_upgrade_and_rerun(
    tmp_path, monkeypatch, upgrade
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    try:
        if upgrade:
            await migrate._install_baseline()
            for name in (
                "024_generation_run.sql",
                "025_provider_connection.sql",
                "026_model_settings.sql",
            ):
                await migrate.run_migration(migrate.DIRECTORY / name)
            async with engine.begin() as db:
                # Foreign keys stay valid; the old session must remain untouched.
                await db.exec_driver_sql(
                    "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES (1,'old','Old','hash','admin','2026-01-01')"
                )
                await db.exec_driver_sql(
                    "INSERT INTO analysis_framework(id,name,labels_json,created_at) VALUES (1,'Old','[]','2026-01-01')"
                )
                await db.exec_driver_sql(
                    "INSERT INTO scenario(id,title,prompt,framework_id,created_at,is_active,tutor_sensitivity) VALUES (1,'Old','Old',1,'2026-01-01',1,'medium')"
                )
                await db.exec_driver_sql(
                    "INSERT INTO session(id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count) VALUES (1,1,1,'2026-01-01',0,0)"
                )
                await db.exec_driver_sql(
                    "INSERT INTO api_usage_log VALUES (7,1,'tutor','old-model',10,2,12,0.012345,'2026-01-01','greeting')"
                )
        await migrate.run_all_migrations()
        await migrate.run_all_migrations()
        async with engine.begin() as db:
            columns = (
                (await db.exec_driver_sql("PRAGMA table_info(model_probe)"))
                .mappings()
                .all()
            )
            assert (
                next(
                    column["notnull"]
                    for column in columns
                    if column["name"] == "owner_id"
                )
                == 1
            )
            references = (
                (
                    await db.exec_driver_sql(
                        "PRAGMA foreign_key_list(model_probe)"
                    )
                )
                .mappings()
                .all()
            )
            assert (
                next(
                    ref["on_delete"]
                    for ref in references
                    if ref["from"] == "owner_id"
                )
                == "RESTRICT"
            )
            assert (
                await db.exec_driver_sql("SELECT count(*) FROM model_probe")
            ).scalar() == 0
            if upgrade:
                row = (
                    (
                        await db.exec_driver_sql(
                            "SELECT * FROM api_usage_log WHERE id=7"
                        )
                    )
                    .mappings()
                    .one()
                )
                assert tuple(
                    row[k]
                    for k in (
                        "id",
                        "session_id",
                        "bot_type",
                        "model",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "estimated_cost_usd",
                        "timestamp",
                        "operation",
                    )
                ) == (
                    7,
                    1,
                    "tutor",
                    "old-model",
                    10,
                    2,
                    12,
                    0.012345,
                    "2026-01-01",
                    "greeting",
                )
                assert (
                    row["invocation_id"] is None
                    and row["status"] is None
                    and row["input_tokens"] is None
                )
            await db.exec_driver_sql(
                "INSERT INTO api_usage_log(invocation_id,request_id,attempt_no,provider,model,role,operation,status,started_at,timestamp) VALUES ('invocation','request',1,'openai','gpt-5-mini','student','probe','running','2026-01-01','2026-01-01')"
            )
            row = (
                (
                    await db.exec_driver_sql(
                        "SELECT * FROM api_usage_log WHERE invocation_id='invocation'"
                    )
                )
                .mappings()
                .one()
            )
            assert all(
                row[k] is None
                for k in (
                    "session_id",
                    "run_id",
                    "owner_id",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                    "estimated_cost_usd",
                    "pricing_as_of",
                    "usage_complete",
                )
            )
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            assert (
                await db.exec_driver_sql(
                    "SELECT count(*) FROM _migrations WHERE filename='027_probe_attempts.sql'"
                )
            ).scalar() == 1
    finally:
        await engine.dispose()

"""030 preserves run replay/history while allowing manual coaching after a check."""

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.migrations import migrate


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_030_preserves_runs_and_enforces_coaching_uniqueness(
    data, monkeypatch
):
    monkeypatch.setattr(migrate, "engine", data.engine)
    for name in [
        "024_generation_run.sql",
        "025_provider_connection.sql",
        "026_model_settings.sql",
        "027_probe_attempts.sql",
        "028_generation_providers.sql",
        "029_scenario_config.sql",
    ]:
        await migrate.run_migration(migrate.DIRECTORY / name)
    async with data.engine.begin() as db:
        await db.exec_driver_sql(
            "INSERT INTO generation_run (id,owner_id,session_id,turn_id,operation,request_id,input_hash,"
            "config_hash,provider,model,status,result_kind,started_at) VALUES "
            "('check',1,1,'turn','mentor','request','input','config','openai','model','completed','no_intervention','2026-01-01')"
        )
        before = (
            await db.exec_driver_sql("SELECT * FROM generation_run")
        ).one()
    await migrate.run_migration(migrate.DIRECTORY / "030_mentor_policy.sql")
    await migrate.run_migration(migrate.DIRECTORY / "030_mentor_policy.sql")
    async with data.engine.begin() as db:
        after = (await db.exec_driver_sql("SELECT * FROM generation_run")).one()
        assert tuple(after[:-1]) == tuple(before) and after[-1] is None
        await db.exec_driver_sql(
            "INSERT INTO generation_run (id,owner_id,session_id,turn_id,operation,request_id,input_hash,"
            "config_hash,provider,model,status,result_kind,started_at,mentor_trigger) VALUES "
            "('coach',1,1,'turn','mentor','manual','input','config','openai','model','completed','message','2026-01-02','manual')"
        )
        assert (
            await db.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (
            await db.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
    with pytest.raises(IntegrityError):
        async with data.engine.begin() as db:
            await db.exec_driver_sql(
                "INSERT INTO generation_run SELECT 'duplicate',owner_id,session_id,turn_id,operation,'another',"
                "input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,"
                "first_output_at,finished_at,mentor_trigger FROM generation_run WHERE id='coach'"
            )
    with pytest.raises(IntegrityError):
        async with data.engine.begin() as db:
            await db.exec_driver_sql(
                "UPDATE generation_run SET mentor_trigger='off' WHERE id='check'"
            )

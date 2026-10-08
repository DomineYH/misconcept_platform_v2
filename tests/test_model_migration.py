"""026 adds model/settings storage without replacing existing connections."""

import json

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.migrations import migrate


@pytest.mark.parametrize("upgrade", [False, True])
async def test_model_settings_fresh_upgrade_rerun(
    tmp_path, monkeypatch, upgrade
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'models.db'}"
    )
    monkeypatch.setattr(migrate, "engine", engine)
    try:
        before = None
        if upgrade:
            await migrate._install_baseline()
            for name in (
                "024_generation_run.sql",
                "025_provider_connection.sql",
            ):
                await migrate.run_migration(migrate.DIRECTORY / name)
            async with engine.begin() as conn:
                await conn.exec_driver_sql(
                    "UPDATE provider_connection SET encrypted_key=zeroblob(17), nonce=zeroblob(12), encryption_key_version='v1', masked_hint='masked', enabled=1, credential_revision=4, connection_version=8 WHERE id=1"
                )
                before = (
                    await conn.exec_driver_sql(
                        "SELECT * FROM provider_connection ORDER BY id"
                    )
                ).all()
        await migrate.run_all_migrations()
        await migrate.run_all_migrations()
        async with engine.connect() as conn:
            assert (
                await conn.exec_driver_sql("SELECT count(*) FROM model_config")
            ).scalar() == 0
            setting = (
                (await conn.exec_driver_sql("SELECT * FROM app_setting"))
                .mappings()
                .one()
            )
            assert setting["id"] == setting["settings_version"] == 1
            assert all(
                setting[f"{role}_model_config_id"] is None
                for role in ("student", "mentor", "analysis")
            )
            assert json.loads(setting["limits_json"]) == dict(
                total=8, openai=4, anthropic=4, google=4, admin=3
            )
            assert json.loads(setting["timeouts_json"]) == dict(
                connect=5,
                student_first_output=60,
                student_total=180,
                mentor_first_output=60,
                mentor_total=180,
                analysis_total=300,
                model_list_total=30,
            )
            assert (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM _migrations WHERE filename='026_model_settings.sql'"
                )
            ).scalar() == 1
            assert (
                await conn.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await conn.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            if before is not None:
                rows = (
                    await conn.exec_driver_sql(
                        "SELECT * FROM provider_connection ORDER BY id"
                    )
                ).all()
                assert [tuple(row[: len(before[0])]) for row in rows] == [
                    tuple(row) for row in before
                ]
    finally:
        await engine.dispose()

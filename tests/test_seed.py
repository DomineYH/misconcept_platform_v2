from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db import seed
from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.models import Scenario, User


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "legacy"])
async def test_seed_installs_and_repeats_without_changing_data(
    tmp_path, monkeypatch, legacy
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(migrate, "engine", engine)
    monkeypatch.setattr(seed, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
        seed.config, "ADMIN_DEFAULT_PASSWORD", "seed-test-password"
    )
    try:
        if legacy:
            async with engine.begin() as conn:
                sql = Path("tests/fixtures/legacy_schema.sql").read_text()
                for statement in migrate.statements(sql):
                    await conn.exec_driver_sql(statement)
                await conn.exec_driver_sql(
                    "INSERT INTO chatbot_config "
                    "(config_key, config_value, config_type) "
                    "VALUES ('student_bot.model', 'keep-existing', 'string')"
                )

        await seed.seed_database()
        async with factory() as db:
            admin = (await db.scalars(select(User))).one()
            scenario = (await db.scalars(select(Scenario))).one()
            assert admin.is_admin and admin.verify_password(
                "seed-test-password"
            )
            assert scenario.created_by == admin.id
            assert scenario.status == "draft" and scenario.config_version == 1
            assert not {
                "framework_id",
                "student_template_id",
                "tutor_template_id",
            } & set(Scenario.__table__.columns.keys())
            assert (
                scenario.config_json["problem"]["public_text"]
                == "1/4 + 1/2은 얼마인가요?"
            )
            assert scenario.config_json["student"]["name"] == "민수"
            assert scenario.config_json["mentor"]["mode"] == "off"
            assert (
                scenario.config_json["student"]["resolved_model_config"] is None
            )
            assert scenario.created_at

        async with engine.connect() as conn:
            assert (
                await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name IN ('analysis_framework','prompt_template')"
                )
            ).all() == []

        tables = [
            "user_group",
            "user",
            "scenario",
        ]
        async with engine.connect() as conn:
            before = {
                table: (
                    await conn.exec_driver_sql(f'SELECT * FROM "{table}"')
                ).all()
                for table in tables
            }
        monkeypatch.setattr(
            seed.config, "ADMIN_DEFAULT_PASSWORD", "must-not-replace"
        )
        await seed.seed_database()
        async with engine.connect() as conn:
            for table in tables:
                assert (
                    await conn.exec_driver_sql(f'SELECT * FROM "{table}"')
                ).all() == before[table]
            assert (
                await conn.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            if legacy:
                assert (
                    await conn.exec_driver_sql(
                        "SELECT config_value FROM chatbot_config"
                    )
                ).scalars().all() == ["keep-existing"]
            else:
                assert not (
                    await conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE name='chatbot_config'"
                    )
                ).first()
    finally:
        await engine.dispose()

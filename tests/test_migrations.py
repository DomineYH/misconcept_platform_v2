import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.db.migrations import migrate


async def test_empty_database_upgrade(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    try:
        await migrate.run_all_migrations()
        await migrate.run_all_migrations()
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM scenario"))).scalar() == 0
    finally:
        await engine.dispose()


async def schema(conn):
    return (await conn.exec_driver_sql("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name != '_migrations' AND name NOT LIKE 'chatbot_%' ORDER BY type, name")).all()


async def test_upgrade_preserves_data_history_and_matches_fresh(data, tmp_path, monkeypatch):
    from src.models import Scenario, PromptTemplate
    monkeypatch.setattr(migrate, "engine", data.engine)
    async with data.engine.begin() as conn:
        await migrate._history(conn)
        await migrate._record(conn, "018_add_scenario_greeting_message.sql")
        await conn.exec_driver_sql("ALTER TABLE analysis_framework DROP COLUMN category_name")
        await conn.exec_driver_sql("ALTER TABLE question_analysis DROP COLUMN grade")
        await conn.exec_driver_sql("ALTER TABLE api_usage_log DROP COLUMN operation")
        await conn.exec_driver_sql("DROP TABLE ui_event")
        await conn.exec_driver_sql("DROP TABLE session_feedback_report")
    await migrate.run_all_migrations()
    await migrate.run_all_migrations()
    async with data.engine.connect() as conn:
        upgraded = await schema(conn)
        assert (await conn.exec_driver_sql("SELECT teacher_id FROM session")).scalar() == data.owner.id
        assert (await conn.exec_driver_sql("PRAGMA foreign_key_check")).all() == []
        assert await migrate._applied(conn, "018_add_scenario_greeting_message.sql")
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(migrate, "engine", fresh)
    await migrate.run_all_migrations()
    async with fresh.connect() as conn:
        assert await schema(conn) == upgraded
    await fresh.dispose()
    template = PromptTemplate(bot_type="student", template_name="test", template_text="test template content")
    data.db.add(template)
    await data.db.flush()
    data.scenario.student_template_id = template.id
    await data.db.commit()
    await data.db.delete(template)
    await data.db.commit()
    await data.db.refresh(data.scenario)
    assert data.scenario.student_template_id is None


async def test_legacy_initializer_normalizes_constraints(tmp_path, monkeypatch):
    from pathlib import Path
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    async with engine.begin() as conn:
        for statement in migrate.statements(Path('tests/fixtures/legacy_schema.sql').read_text()):
            await conn.exec_driver_sql(statement)
    await migrate.run_all_migrations()
    async with engine.connect() as conn:
        columns = {r[1]: r for r in await conn.exec_driver_sql('PRAGMA table_info(scenario)')}
        assert columns['student_template_id'][3] == 0
        fks = {r[3]: r for r in await conn.exec_driver_sql('PRAGMA foreign_key_list(scenario)')}
        assert fks['student_template_id'][6] == 'SET NULL'
    await engine.dispose()


async def test_ddl_failure_rolls_back_history_and_schema(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    await migrate.run_all_migrations()
    migration = tmp_path / '024_test.sql'
    migration.write_text("CREATE TABLE probe (value TEXT); INSERT INTO missing VALUES (1);")
    with pytest.raises(Exception):
        await migrate.run_migration(migration)
    async with engine.connect() as conn:
        assert not await migrate._applied(conn, migration.name)
        assert not (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='probe'")).first()
    migration.write_text("CREATE TABLE probe (value TEXT); INSERT INTO probe VALUES ('a;b');")
    await migrate.run_migration(migration)
    await migrate.run_migration(migration)
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql("SELECT value FROM probe")).all() == [('a;b',)]
    await engine.dispose()


async def test_official_install_starts_app(tmp_path, monkeypatch):
    from src.main import app, lifespan
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}")
    monkeypatch.setattr(migrate, 'engine', engine)
    async with lifespan(app):
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql('SELECT count(*) FROM "user"')).scalar() == 0
    await engine.dispose()

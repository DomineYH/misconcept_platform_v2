import asyncio
import json
import os
import sys
import textwrap

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from test_demo_d2_01_schema import REBUILT, database_state, populated036

from src.db.migrations import migrate

__all__ = ["populated036"]


async def test_empty_database_upgrade(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr(migrate, "engine", engine)
    try:
        await migrate.run_all_migrations()
        await migrate.run_all_migrations()
        async with engine.connect() as conn:
            assert (
                await conn.execute(text("SELECT count(*) FROM scenario"))
            ).scalar() == 0
    finally:
        await engine.dispose()


async def schema(conn):
    return (
        await conn.exec_driver_sql(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name != '_migrations' AND name NOT LIKE 'chatbot_%' ORDER BY type, name"
        )
    ).all()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_upgrade_preserves_data_history_and_matches_fresh(
    data, tmp_path, monkeypatch
):
    from legacy_models import PromptTemplate

    monkeypatch.setattr(migrate, "engine", data.engine)
    async with data.engine.begin() as conn:
        await migrate._history(conn)
        await migrate._record(conn, "018_add_scenario_greeting_message.sql")
        await conn.exec_driver_sql(
            "ALTER TABLE analysis_framework DROP COLUMN category_name"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE question_analysis DROP COLUMN grade"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE api_usage_log DROP COLUMN operation"
        )
        await conn.exec_driver_sql("DROP TABLE ui_event")
        await conn.exec_driver_sql("DROP TABLE session_feedback_report")
    await migrate.run_all_migrations(through=30)
    await migrate.run_all_migrations(through=30)
    async with data.engine.connect() as conn:
        upgraded = await schema(conn)
        assert (
            await conn.exec_driver_sql("SELECT teacher_id FROM session")
        ).scalar() == data.owner.id
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert await migrate._applied(
            conn, "018_add_scenario_greeting_message.sql"
        )
    fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(migrate, "engine", fresh)
    await migrate.run_all_migrations(through=30)
    async with fresh.connect() as conn:
        assert await schema(conn) == upgraded
    await fresh.dispose()
    template = PromptTemplate(
        bot_type="student",
        template_name="test",
        template_text="test template content",
    )
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

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}"
    )
    monkeypatch.setattr(migrate, "engine", engine)
    async with engine.begin() as conn:
        for statement in migrate.statements(
            Path("tests/fixtures/legacy_schema.sql").read_text()
        ):
            await conn.exec_driver_sql(statement)
    await migrate.run_all_migrations(through=30)
    async with engine.connect() as conn:
        columns = {
            r[1]: r
            for r in await conn.exec_driver_sql("PRAGMA table_info(scenario)")
        }
        assert columns["student_template_id"][3] == 0
        fks = {
            r[3]: r
            for r in await conn.exec_driver_sql(
                "PRAGMA foreign_key_list(scenario)"
            )
        }
        assert fks["student_template_id"][6] == "SET NULL"
    await engine.dispose()


async def test_ddl_failure_rolls_back_history_and_schema(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}"
    )
    monkeypatch.setattr(migrate, "engine", engine)
    await migrate.run_all_migrations()
    migration = tmp_path / "024_test.sql"
    migration.write_text(
        "CREATE TABLE probe (value TEXT); INSERT INTO missing VALUES (1);"
    )
    with pytest.raises(Exception):
        await migrate.run_migration(migration)
    async with engine.connect() as conn:
        assert not await migrate._applied(conn, migration.name)
        assert not (
            await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE name='probe'"
            )
        ).first()
    migration.write_text(
        "CREATE TABLE probe (value TEXT); INSERT INTO probe VALUES ('a;b');"
    )
    await migrate.run_migration(migration)
    await migrate.run_migration(migration)
    async with engine.connect() as conn:
        assert (
            await conn.exec_driver_sql("SELECT value FROM probe")
        ).all() == [("a;b",)]
    await engine.dispose()


async def test_official_install_starts_app(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.db import connection
    from src.main import app, lifespan

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}"
    )
    monkeypatch.setattr(migrate, "engine", engine)
    monkeypatch.setattr(
        connection,
        "AsyncSessionLocal",
        async_sessionmaker(engine, expire_on_commit=False),
    )
    async with lifespan(app):
        async with engine.connect() as conn:
            assert (
                await conn.exec_driver_sql('SELECT count(*) FROM "user"')
            ).scalar() == 0
    await engine.dispose()


@pytest.mark.parametrize("entry", ["import", "module"])
async def test_demo_cli_refuses_before_database_import_or_access(entry):
    script = textwrap.dedent(
        """
        import pathlib
        import runpy
        import sqlite3
        import sys

        def forbidden(*args, **kwargs):
            raise AssertionError("Migration CLI accessed database/files")

        def audit(event, args):
            if event == "import" and args[0] in {"src.db.connection", "src.config"}:
                raise AssertionError("Migration CLI imported application database/config")

        sys.addaudithook(audit)
        pathlib.Path.read_text = forbidden
        sqlite3.connect = forbidden
        sys.argv = ["migrate", "--through", "37"]
        if sys.argv_entry == "module":
            runpy.run_module("src.db.migrations.migrate", run_name="__main__")
        else:
            from src.db.migrations.migrate import main
            raise SystemExit(main())
        """
    ).replace("sys.argv_entry", repr(entry))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        env={**os.environ, "APP_MODE": "demo"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 1, stderr.decode()
    assert json.loads(stdout) == {
        "code": "DEMO_ACTION_FORBIDDEN",
        "exit_status": 1,
    }


async def test_demo_explicit_engine_installs_without_global_access(tmp_path):
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from sqlalchemy.ext.asyncio import create_async_engine
        from src.db import connection
        from src.db.migrations import migrate

        class FalseyEngine:
            def __bool__(self):
                return False

            def connect(self):
                return staging.connect()

        class ForbiddenEngine:
            def connect(self):
                raise AssertionError("Explicit engine accessed global engine")

        staging = create_async_engine("sqlite+aiosqlite:///" + sys.argv[1])
        connection.engine = migrate.engine = ForbiddenEngine()

        async def install():
            try:
                await migrate.run_all_migrations(through=37, db_engine=FalseyEngine())
                async with staging.connect() as db:
                    assert await migrate._applied(db, "037_demo_provider.sql")
                    assert (await db.exec_driver_sql("PRAGMA foreign_key_check")).all() == []
            finally:
                await staging.dispose()

        asyncio.run(install())
        """
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(tmp_path / "staging.db"),
        env={**os.environ, "APP_MODE": "demo"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()


@pytest.mark.parametrize("failure", [None, "sql", "foreign_key", "object"])
async def test_demo_rebuild_preserves_objects_and_rolls_back(tmp_path, failure):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'rebuild.db'}"
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    tables = ("provider_connection", "provider_audit_log", "generation_run")
    migration = tmp_path / "037_demo_provider.sql"
    rebuild = []
    try:
        async with engine.begin() as db:
            await migrate._history(db)
            await migrate._record(db, "031_scenario_contract.sql")
            await db.exec_driver_sql("CREATE TABLE trace (name TEXT)")
            for table in tables:
                parent = (
                    ", parent INTEGER REFERENCES provider_connection(id)"
                    if table != "provider_connection"
                    else ""
                )
                await db.exec_driver_sql(
                    f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT{parent})"
                )
                values = "1,'before',1" if parent else "1,'before'"
                await db.exec_driver_sql(
                    f"INSERT INTO {table} VALUES ({values})"
                )
                await db.exec_driver_sql(
                    f"CREATE UNIQUE INDEX custom_{table} ON {table}(lower(value)) WHERE value IS NOT NULL"
                )
                await db.exec_driver_sql(
                    f"CREATE TRIGGER watch_{table} AFTER UPDATE ON {table} BEGIN INSERT INTO trace VALUES ('{table}'); END"
                )
                value_column = (
                    "other"
                    if failure == "object" and table == tables[-1]
                    else "value"
                )
                rebuild.extend(
                    (
                        f"CREATE TABLE {table}_new (id INTEGER PRIMARY KEY, {value_column} TEXT{parent});",
                        f"INSERT INTO {table}_new SELECT * FROM {table};",
                        f"DROP TABLE {table};",
                        f"ALTER TABLE {table}_new RENAME TO {table};",
                    )
                )
            before = await schema(db)
            history = (
                await db.exec_driver_sql(
                    "SELECT * FROM _migrations ORDER BY id"
                )
            ).all()
        if failure == "sql":
            rebuild.append("INSERT INTO missing VALUES (1);")
        elif failure == "foreign_key":
            rebuild.append(
                "INSERT INTO provider_audit_log VALUES (2,'orphan',99);"
            )
        migration.write_text("\n".join(rebuild))
        if failure:
            error = ValueError if failure == "foreign_key" else OperationalError
            with pytest.raises(error):
                await migrate.run_migration(migration, db_engine=engine)
        else:
            await migrate.run_migration(migration, db_engine=engine)
            await migrate.run_migration(migration, db_engine=engine)
        async with engine.begin() as db:
            assert (
                await db.exec_driver_sql("PRAGMA foreign_keys")
            ).scalar() == 1
            assert (
                await db.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
            assert (
                await db.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            objects = [row for row in before if row[0] in ("index", "trigger")]
            assert [
                row
                for row in await schema(db)
                if row[0] in ("index", "trigger")
            ] == objects
            for table in tables:
                values = (
                    [(1, "before", 1)]
                    if table != tables[0]
                    else [(1, "before")]
                )
                assert (
                    await db.exec_driver_sql(f"SELECT * FROM {table}")
                ).all() == values
                await db.exec_driver_sql(
                    f"UPDATE {table} SET value='after' WHERE id=1"
                )
            assert (
                await db.exec_driver_sql("SELECT name FROM trace ORDER BY name")
            ).scalars().all() == sorted(tables)
            after_history = (
                await db.exec_driver_sql(
                    "SELECT * FROM _migrations ORDER BY id"
                )
            ).all()
            if failure:
                assert await schema(db) == before
                assert after_history == history
            else:
                assert after_history[:-1] == history
                assert after_history[-1][1] == migration.name
    finally:
        await engine.dispose()


@pytest.mark.parametrize("legacy_alter_table", [0, 1])
@pytest.mark.parametrize("failure", [False, True], ids=["upgrade", "rollback"])
async def test_037_preserves_dependent_views_and_cross_table_trigger(
    populated036, tmp_path, legacy_alter_table, failure
):
    expected = {
        "provider_connection": [1, 2, 3],
        "provider_audit_log": [1],
        "generation_run": ["analysis", "comparison-panel", "student"],
    }
    async with populated036.begin() as db:
        await db.exec_driver_sql(
            f"PRAGMA legacy_alter_table={legacy_alter_table}"
        )
        for table in REBUILT:
            await db.exec_driver_sql(
                f"CREATE VIEW dependent_{table} AS SELECT id FROM {table}"
            )
        await db.exec_driver_sql(
            "CREATE TRIGGER cross_rebuild AFTER INSERT ON extra_trace BEGIN "
            "SELECT id FROM provider_connection; "
            "SELECT id FROM provider_audit_log; "
            "SELECT id FROM generation_run; END"
        )
        before = await database_state(db)
        for table, ids in expected.items():
            assert (
                await db.exec_driver_sql(
                    f"SELECT id FROM dependent_{table} ORDER BY id"
                )
            ).scalars().all() == ids
    migration = migrate.DIRECTORY / "037_demo_provider.sql"
    if failure:
        broken = tmp_path / migration.name
        broken.write_text(
            migration.read_text()
            + "\nINSERT INTO repair_missing_table VALUES (1);"
        )
        with pytest.raises(OperationalError, match="repair_missing_table"):
            await migrate.run_migration(broken, db_engine=populated036)
    else:
        await migrate.run_all_migrations(db_engine=populated036)
    async with populated036.begin() as db:
        assert (
            await db.exec_driver_sql("PRAGMA legacy_alter_table")
        ).scalar() == legacy_alter_table
        assert (await db.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        after = await database_state(db)
        if failure:
            assert after == before
        else:
            assert {
                name: after["foreign_keys"][name]
                for name in before["foreign_keys"]
            } == before["foreign_keys"]
            assert [
                row
                for row in after["objects"]
                if row[0] == "view" or row[1] == "cross_rebuild"
            ] == [
                row
                for row in before["objects"]
                if row[0] == "view" or row[1] == "cross_rebuild"
            ]
        for table, ids in expected.items():
            assert (
                await db.exec_driver_sql(
                    f"SELECT id FROM dependent_{table} ORDER BY id"
                )
            ).scalars().all() == ids
        await db.exec_driver_sql(
            "INSERT INTO extra_trace VALUES ('cross-trigger-probe')"
        )
        assert (
            await db.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (
            await db.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"

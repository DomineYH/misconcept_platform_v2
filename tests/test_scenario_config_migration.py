"""029 expands authoring without changing historical records."""

import pytest

from src.db.migrations import migrate


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_config_expansion_preserves_legacy_and_accepts_template_free_draft(
    data, monkeypatch
):
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate._install_baseline()
    for path in sorted(migrate.DIRECTORY.glob("[0-9]*.sql")):
        if 24 <= int(path.name.split("_")[0]) <= 28:
            await migrate.run_migration(path)
    scenario_indexes = {
        "idx_scenario_active": ["is_active"],
        "idx_scenario_deleted": ["deleted_at"],
        "idx_scenario_framework": ["framework_id"],
        "idx_scenario_student_template": ["student_template_id"],
        "idx_scenario_tutor_template": ["tutor_template_id"],
    }
    async with data.engine.begin() as conn:
        for name, columns in scenario_indexes.items():
            await conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {name} ON scenario({columns[0]})"
            )
        await conn.exec_driver_sql(
            "INSERT INTO message(id,session_id,role,content,created_at) "
            "VALUES (42,1,'teacher','Preserved dialogue {x}','2026-01-01')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO question_analysis(id,message_id,label,grade,confidence,meta_json) "
            "VALUES (43,42,'Original label','high',0.8,'{}')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO session_summary(id,session_id,distribution_json,feedback) "
            "VALUES (44,1,'{}','Original successful result')"
        )
        before = {
            name: (await conn.exec_driver_sql(f"SELECT * FROM {name}")).all()
            for name in (
                "scenario",
                "scenario_group",
                "session",
                "user",
                "analysis_framework",
                "message",
                "question_analysis",
                "session_summary",
            )
        }
        old_columns = {
            name: ",".join(
                r[1]
                for r in await conn.exec_driver_sql(
                    f"PRAGMA table_info({name})"
                )
            )
            for name in before
        }
    await migrate.run_all_migrations(through=30)
    await migrate.run_all_migrations(through=30)
    async with data.engine.begin() as conn:
        assert {
            index[1]: [
                column[2]
                for column in await conn.exec_driver_sql(
                    f"PRAGMA index_info({index[1]})"
                )
            ]
            for index in await conn.exec_driver_sql(
                "PRAGMA index_list(scenario)"
            )
        } == scenario_indexes
        for name, rows in before.items():
            columns = old_columns[name]
            assert (
                await conn.exec_driver_sql(f"SELECT {columns} FROM {name}")
            ).all() == rows
        assert await migrate._applied(conn, "029_scenario_config.sql")
        await conn.exec_driver_sql(
            "INSERT INTO scenario (id,title,is_active,tutor_sensitivity,created_at,status,config_json) "
            "VALUES (2,'Draft',1,'medium','2026-10-09','draft','{}')"
        )
        assert (
            await conn.exec_driver_sql(
                "SELECT prompt,framework_id,config_schema_version,config_version,review_required FROM scenario WHERE id=2"
            )
        ).one() == (None, None, 1, 1, 0)
        assert (
            await conn.exec_driver_sql(
                "SELECT config_snapshot_json,config_hash,source_scenario_version,snapshot_origin,snapshot_created_at FROM session"
            )
        ).one() == (None, None, None, None, None)
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_config_expansion_failure_rolls_back_ddl_and_history(
    data, monkeypatch
):
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate._install_baseline()
    for path in sorted(migrate.DIRECTORY.glob("[0-9]*.sql")):
        if 24 <= int(path.name.split("_")[0]) <= 28:
            await migrate.run_migration(path)
    async with data.engine.connect() as conn:
        before = (await conn.exec_driver_sql("SELECT * FROM scenario")).all()
    record = migrate._record

    async def fail_history(conn, filename):
        if filename == "029_scenario_config.sql":
            raise RuntimeError("Injected expansion failure")
        await record(conn, filename)

    monkeypatch.setattr(migrate, "_record", fail_history)
    with pytest.raises(RuntimeError, match="Injected expansion failure"):
        await migrate.run_migration(
            migrate.DIRECTORY / "029_scenario_config.sql"
        )
    async with data.engine.connect() as conn:
        assert (
            await conn.exec_driver_sql("SELECT * FROM scenario")
        ).all() == before
        assert "config_json" not in [
            r[1]
            for r in await conn.exec_driver_sql("PRAGMA table_info(scenario)")
        ]
        assert "config_snapshot_json" not in [
            r[1]
            for r in await conn.exec_driver_sql("PRAGMA table_info(session)")
        ]
        assert not await migrate._applied(conn, "029_scenario_config.sql")
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []

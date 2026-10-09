"""028 preserves run identity, child references and execution constraints."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.db.migrations import migrate

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize(
    "commit_failure", [None, "before_begin", "after_rebuild"]
)
async def test_provider_expansion_preserves_references_and_unique_rights(
    data, monkeypatch, commit_failure
):
    monkeypatch.setattr(migrate, "engine", data.engine)
    # Prepare the deployed A6 schema with populated run/message/ledger links.
    await migrate._install_baseline()
    for path in sorted(migrate.DIRECTORY.glob("[0-9]*.sql")):
        if 24 <= int(path.name.split("_")[0]) <= 27:
            await migrate.run_migration(path)
    async with data.engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO generation_run VALUES ('old',1,1,'turn','student','request','input','config','openai','gpt-5-mini','completed',NULL,'message',NULL,'2026-01-01',NULL,'2026-01-01')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO message (id,session_id,role,content,created_at,turn_id,turn_index,generation_run_id) VALUES (10,1,'student','Preserved','2026-01-01','turn',1,'old')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO api_usage_log (id,session_id,run_id,timestamp,status,total_tokens) VALUES (20,1,'old','2026-01-01','completed',12)"
        )
        before = (
            await conn.exec_driver_sql("SELECT * FROM generation_run")
        ).all()
    if commit_failure:
        from sqlalchemy import event

        reject_next = commit_failure == "before_begin"

        def record_rebuild(conn, cursor, statement, parameters, context, many):
            nonlocal reject_next
            if (
                statement.startswith("INSERT INTO _migrations")
                and "028_generation_providers.sql" in parameters
            ):
                reject_next = True

        def reject_commit(conn):
            nonlocal reject_next
            if reject_next:
                reject_next = False
                raise RuntimeError("Injected migration commit failure")

        event.listen(data.engine.sync_engine, "commit", reject_commit)
        if commit_failure == "after_rebuild":
            event.listen(
                data.engine.sync_engine, "before_cursor_execute", record_rebuild
            )
        try:
            with pytest.raises(
                RuntimeError, match="Injected migration commit failure"
            ):
                await migrate.run_migration(
                    migrate.DIRECTORY / "028_generation_providers.sql"
                )
        finally:
            event.remove(data.engine.sync_engine, "commit", reject_commit)
            if commit_failure == "after_rebuild":
                event.remove(
                    data.engine.sync_engine,
                    "before_cursor_execute",
                    record_rebuild,
                )
        async with data.engine.connect() as conn:
            assert (
                await conn.exec_driver_sql("PRAGMA foreign_keys")
            ).scalar() == 1
            assert (
                await conn.exec_driver_sql("SELECT * FROM generation_run")
            ).all() == before
            assert (
                await conn.exec_driver_sql(
                    "SELECT generation_run_id FROM message WHERE id=10"
                )
            ).scalar() == "old"
            assert (
                await conn.exec_driver_sql(
                    "SELECT run_id FROM api_usage_log WHERE id=20"
                )
            ).scalar() == "old"
            assert not await migrate._applied(
                conn, "028_generation_providers.sql"
            )
    await migrate.run_all_migrations()
    await migrate.run_all_migrations()
    async with data.engine.begin() as conn:
        assert (
            await conn.exec_driver_sql("SELECT * FROM generation_run")
        ).all() == [tuple(row) + (None,) for row in before]
        assert (
            await conn.exec_driver_sql(
                "SELECT generation_run_id,content FROM message WHERE id=10"
            )
        ).one() == ("old", "Preserved")
        assert (
            await conn.exec_driver_sql(
                "SELECT run_id,total_tokens FROM api_usage_log WHERE id=20"
            )
        ).one() == ("old", 12)
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        for provider in ("anthropic", "google"):
            await conn.execute(
                text(
                    "INSERT INTO generation_run SELECT :id,owner_id,session_id,:turn,operation,:request,input_hash,config_hash,:provider,model,'failed',partial_text,NULL,error_code,started_at,first_output_at,finished_at,mentor_trigger FROM generation_run WHERE id='old'"
                ),
                dict(
                    id=provider,
                    turn=provider,
                    request=provider,
                    provider=provider,
                ),
            )
    for change in (
        "provider='unknown'",
        "status='completed',turn_id='turn'",
        "request_id='request'",
    ):
        with pytest.raises(IntegrityError):
            async with data.engine.begin() as conn:
                await conn.exec_driver_sql(
                    f"UPDATE generation_run SET {change} WHERE id='google'"
                )

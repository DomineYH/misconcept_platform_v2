"""D2-01: official demo schema install, upgrade and database constraints."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import JSON, UniqueConstraint, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.connection import Base
from src.db.migrations import migrate
from src.models import Message, Scenario, Session, User
from src.models.api_usage import ApiUsageLog
from src.models.comparison_run import ComparisonRun
from src.models.demo_metadata import DemoMetadata
from src.models.generation_run import GenerationRun
from src.models.model_config import ModelConfig
from src.models.provider_connection import ProviderAuditLog, ProviderConnection
from src.models.session_feedback_report import SessionFeedbackReport

REVISION = "037_demo_provider.sql"
NOW = datetime(2026, 1, 1)
REBUILT = ("provider_connection", "provider_audit_log", "generation_run")


def database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture
async def installed(tmp_path):
    engine = database(tmp_path / "schema.db")
    try:
        await migrate.run_all_migrations(db_engine=engine)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(params=["migrated", "orm"])
async def constrained(request, tmp_path):
    engine = database(tmp_path / f"{request.param}.db")
    try:
        if request.param == "migrated":
            await migrate.run_all_migrations(db_engine=engine)
        else:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                for provider_id, provider in enumerate(
                    ("openai", "anthropic", "google"), 1
                ):
                    await conn.execute(
                        ProviderConnection.__table__.insert().values(
                            id=provider_id, provider=provider
                        )
                    )
        yield engine
    finally:
        await engine.dispose()


async def test_normal_install_has_empty_metadata_and_no_demo_seed(installed):
    async with installed.connect() as conn:
        assert (
            await conn.exec_driver_sql("SELECT * FROM demo_metadata")
        ).all() == []
        assert (
            await conn.exec_driver_sql(
                "SELECT provider FROM provider_connection ORDER BY id"
            )
        ).scalars().all() == ["openai", "anthropic", "google"]
        for table in (
            "user",
            "scenario",
            "session",
            "model_config",
            "provider_audit_log",
        ):
            assert (
                await conn.exec_driver_sql(f'SELECT count(*) FROM "{table}"')
            ).scalar() == 0
        assert (
            await conn.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
        assert (
            await conn.exec_driver_sql(
                "SELECT filename FROM _migrations ORDER BY id DESC LIMIT 1"
            )
        ).scalar() == "037_demo_provider.sql"


async def reject(conn, sql, parameters=()):
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await conn.exec_driver_sql(sql, parameters)


async def test_demo_and_external_provider_key_material(constrained):
    async with constrained.begin() as conn:
        await conn.execute(
            ProviderConnection.__table__.insert().values(
                id=4, provider="demo", enabled=True
            )
        )
        for assignment, value in (
            ("encrypted_key", b""),
            ("encrypted_key", b"x" * 17),
            ("nonce", b""),
            ("nonce", b"x" * 12),
            ("encryption_key_version", ""),
            ("encryption_key_version", "v1"),
            ("masked_hint", ""),
            ("masked_hint", "hint"),
            ("credential_revision", 1),
            ("credential_revision", -1),
            ("connection_version", 0),
            ("enabled", 2),
            ("catalog_models_json", "invalid-json"),
        ):
            await reject(
                conn,
                f"UPDATE provider_connection SET {assignment}=? WHERE id=4",
                (value,),
            )
        await conn.exec_driver_sql(
            "UPDATE provider_connection SET enabled=0,connection_version=2 WHERE id=4"
        )
        for provider in ("demo", "unknown"):
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(
                        ProviderConnection.__table__.insert().values(
                            provider=provider
                        )
                    )
        for provider_id in (1, 2, 3):
            await reject(
                conn,
                "UPDATE provider_connection SET enabled=1 WHERE id=?",
                (provider_id,),
            )
            await conn.exec_driver_sql(
                "UPDATE provider_connection SET encrypted_key=?,nonce=?,encryption_key_version='v1',masked_hint='hint',enabled=1,credential_revision=1 WHERE id=?",
                (b"x" * 17, b"n" * 12, provider_id),
            )
            for assignment, value in (
                ("encrypted_key", None),
                ("encrypted_key", b"x" * 16),
                ("nonce", None),
                ("nonce", b"n" * 11),
                ("encryption_key_version", None),
                ("encryption_key_version", ""),
                ("masked_hint", None),
                ("credential_revision", -1),
                ("connection_version", 0),
            ):
                await reject(
                    conn,
                    f"UPDATE provider_connection SET {assignment}=? WHERE id=?",
                    (value, provider_id),
                )


async def test_metadata_seed_and_visitor_marker_constraints(constrained):
    async with constrained.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO demo_metadata VALUES(1,'demo_seed','d0-03-v1-r3','d0-02-v1','demo-fixture-v1','037_demo_provider.sql',?,NULL,NULL)",
            ("a" * 64,),
        )
        for assignment, value in (
            ("id", 2),
            ("kind", "other"),
            ("kind", "demo_visitor"),
            ("visitor_id", "visitor"),
            ("generation", 1),
            ("manifest_sha256", "x" * 64),
            ("manifest_sha256", "a" * 63),
            ("contract_version", None),
            ("content_version", None),
            ("fixture_version", None),
            ("db_schema_version", None),
        ):
            await reject(
                conn, f"UPDATE demo_metadata SET {assignment}=?", (value,)
            )
        await reject(
            conn, "INSERT INTO demo_metadata SELECT * FROM demo_metadata"
        )
        await conn.exec_driver_sql(
            "UPDATE demo_metadata SET kind='demo_visitor',visitor_id='9e0a5026-8e04-4621-870d-66f359e8aefe',generation=1 WHERE id=1"
        )
        for assignment, value in (
            ("visitor_id", None),
            ("visitor_id", ""),
            ("generation", None),
            ("generation", 0),
            ("generation", -1),
            ("generation", 1.5),
            ("kind", "demo_seed"),
        ):
            await reject(
                conn, f"UPDATE demo_metadata SET {assignment}=?", (value,)
            )


async def seed_classroom(conn):
    await conn.execute(
        User.__table__.insert().values(
            id=1,
            username="admin",
            nickname="Admin",
            password_hash="hash",
            role="admin",
            created_at=NOW,
        )
    )
    await conn.execute(
        Scenario.__table__.insert().values(
            id=1, title="Original", config_json={}, created_at=NOW
        )
    )
    await conn.execute(
        Session.__table__.insert().values(
            id=1, scenario_id=1, teacher_id=1, started_at=NOW
        )
    )
    await conn.execute(
        ComparisonRun.__table__.insert().values(
            id="comparison",
            admin_id=1,
            scenario_id=1,
            source_snapshot={},
            source_hash="source",
            target_role="student",
            mode="same_context",
            canonical_context=[],
            context_hash="context",
            panel_configs=[],
            request_id="comparison-request",
            input_hash="input",
            created_at=NOW,
            expires_at=NOW,
            status="finished",
        )
    )


async def add_run(conn, run_id, **changes):
    values = dict(
        id=run_id,
        owner_id=1,
        session_id=1,
        turn_id=run_id,
        operation="student",
        request_id=run_id,
        input_hash="input",
        config_hash="config",
        provider="demo",
        model="example",
        status="failed",
        started_at=NOW,
    )
    values.update(changes)
    await conn.execute(GenerationRun.__table__.insert().values(**values))


async def reject_run(conn, run_id, **changes):
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await add_run(conn, run_id, **changes)


async def test_audit_demo_seed_and_external_change_contract(constrained):
    async with constrained.begin() as conn:
        await seed_classroom(conn)
        await conn.execute(
            ProviderConnection.__table__.insert().values(
                id=4, provider="demo", enabled=True
            )
        )
        values = dict(
            provider_connection_id=4,
            provider="demo",
            change_kind="demo_seeded",
            actor_id=None,
            previous_credential_revision=0,
            credential_revision=0,
            previous_connection_version=1,
            connection_version=1,
        )
        statement = ProviderAuditLog.__table__.insert()
        await conn.execute(statement.values(**values))
        for provider_id, provider in enumerate(
            ("openai", "anthropic", "google"), 1
        ):
            for kind in (
                "key_saved",
                "key_replaced",
                "enabled",
                "disabled",
                "deleted",
            ):
                await conn.execute(
                    statement.values(
                        **{
                            **values,
                            "provider_connection_id": provider_id,
                            "provider": provider,
                            "change_kind": kind,
                            "actor_id": 1,
                            "credential_revision": 2,
                            "connection_version": 3,
                        }
                    )
                )
        invalid = [
            {"change_kind": kind}
            for kind in (
                "key_saved",
                "key_replaced",
                "enabled",
                "disabled",
                "deleted",
                "unknown",
            )
        ]
        invalid += [
            {"provider": provider, "provider_connection_id": provider_id}
            for provider_id, provider in enumerate(
                ("openai", "anthropic", "google"), 1
            )
        ]
        invalid += [
            {field: value}
            for field, value in (
                ("actor_id", 1),
                ("previous_credential_revision", 1),
                ("credential_revision", 1),
                ("previous_connection_version", 0),
                ("previous_connection_version", 2),
                ("connection_version", 0),
                ("connection_version", 2),
                ("provider_connection_id", 999),
            )
        ]
        for changes in invalid:
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(
                        statement.values(**{**values, **changes})
                    )
        assert (
            await conn.exec_driver_sql(
                "SELECT count(*) FROM provider_audit_log"
            )
        ).scalar() == 16


async def test_generation_membership_checks_and_partial_indexes(constrained):
    async with constrained.begin() as conn:
        await seed_classroom(conn)
        for provider in ("openai", "anthropic", "google", "demo"):
            await add_run(conn, provider, provider=provider)
        for number, changes in enumerate(
            (
                {"provider": "unknown"},
                {"operation": "unknown"},
                {"status": "unknown"},
                {"session_id": None},
                {"session_id": 999},
                {"owner_id": 999},
                {
                    "session_id": None,
                    "comparison_run_id": "missing",
                    "panel_id": "panel",
                },
                {"comparison_run_id": "comparison", "panel_id": "panel"},
                {"session_id": None, "comparison_run_id": "comparison"},
                {"panel_id": "panel"},
                {"operation": "analysis", "status": "completed"},
                {"status": "ok"},
                {"operation": "mentor", "status": "degraded"},
                {"mentor_trigger": "unknown"},
                {"result_kind": "unknown"},
            )
        ):
            await reject_run(conn, f"invalid-{number}", **changes)
        for status in ("ok", "degraded"):
            await add_run(
                conn, f"analysis-{status}", operation="analysis", status=status
            )
        for trigger in ("manual", "auto"):
            await add_run(
                conn,
                f"mentor-{trigger}",
                operation="mentor",
                mentor_trigger=trigger,
                result_kind="no_intervention",
            )
        for operation in ("student", "mentor", "analysis"):
            await add_run(
                conn,
                f"running-{operation}",
                operation=operation,
                status="running",
            )
            await reject_run(
                conn,
                f"duplicate-running-{operation}",
                operation=operation,
                status="running",
            )
        await add_run(
            conn,
            "completed-student",
            status="completed",
            turn_id="completed-turn",
        )
        await reject_run(
            conn,
            "completed-again",
            status="completed",
            turn_id="completed-turn",
        )
        await add_run(
            conn,
            "completed-mentor",
            operation="mentor",
            status="completed",
            result_kind="message",
            turn_id="completed-turn",
        )
        await reject_run(
            conn,
            "completed-mentor-again",
            operation="mentor",
            status="completed",
            result_kind="message",
            turn_id="completed-turn",
        )
        await add_run(
            conn,
            "no-intervention",
            operation="mentor",
            status="completed",
            result_kind="no_intervention",
            turn_id="completed-turn",
        )
        await reject_run(conn, "request-replay", request_id="demo")
        comparison = dict(
            session_id=None, comparison_run_id="comparison", panel_id="left"
        )
        await add_run(
            conn, "comparison-running", status="running", **comparison
        )
        await reject_run(
            conn, "comparison-busy", status="running", **comparison
        )
        await add_run(
            conn,
            "comparison-other-panel",
            status="running",
            **{**comparison, "panel_id": "right"},
        )
        await reject_run(
            conn,
            "comparison-replay",
            request_id="comparison-running",
            **comparison,
        )
        await add_run(conn, "comparison-other-request", **comparison)
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []


async def test_generation_ledger_children_and_delete_actions(constrained):
    async with constrained.begin() as conn:
        await seed_classroom(conn)
        await add_run(conn, "classroom")
        await add_run(
            conn,
            "comparison-child",
            session_id=None,
            comparison_run_id="comparison",
            panel_id="left",
        )
        await conn.execute(
            Message.__table__.insert().values(
                id=1,
                session_id=1,
                role="student",
                content="Saved",
                generation_run_id="classroom",
            )
        )
        await conn.execute(
            ApiUsageLog.__table__.insert().values(
                id=1,
                session_id=1,
                run_id="classroom",
                owner_id=1,
                invocation_id="invocation",
                attempt_no=1,
                provider="demo",
                usage_scope="classroom",
                timestamp=NOW,
            )
        )
        await reject(
            conn,
            "INSERT INTO api_usage_log(timestamp,run_id) VALUES('2026-01-01','missing')",
        )
        await reject(
            conn,
            "INSERT INTO api_usage_log(timestamp,invocation_id,attempt_no) VALUES('2026-01-01','invocation',1)",
        )
        await reject(
            conn, "UPDATE api_usage_log SET usage_scope='other' WHERE id=1"
        )
        await reject(
            conn, "UPDATE message SET generation_run_id='missing' WHERE id=1"
        )
        await reject(conn, "DELETE FROM generation_run WHERE id='classroom'")
        await conn.exec_driver_sql("DELETE FROM api_usage_log WHERE id=1")
        await conn.exec_driver_sql(
            "DELETE FROM generation_run WHERE id='classroom'"
        )
        assert (
            await conn.exec_driver_sql(
                "SELECT generation_run_id FROM message WHERE id=1"
            )
        ).scalar() is None
        await conn.exec_driver_sql(
            "DELETE FROM comparison_run WHERE id='comparison'"
        )
        assert (
            await conn.exec_driver_sql(
                "SELECT count(*) FROM generation_run WHERE id='comparison-child'"
            )
        ).scalar() == 0
        await add_run(conn, "cascade-session")
        await conn.exec_driver_sql("DELETE FROM session WHERE id=1")
        assert (
            await conn.exec_driver_sql("SELECT count(*) FROM generation_run")
        ).scalar() == 0
        assert (
            await conn.exec_driver_sql("SELECT count(*) FROM message")
        ).scalar() == 0
        await conn.exec_driver_sql(
            "INSERT INTO session(id,scenario_id,teacher_id,started_at,tutor_intervention_count,tutor_question_count) VALUES(2,1,1,'2026-01-01',0,0)"
        )
        await add_run(conn, "orphan-owner", session_id=2)
        await conn.exec_driver_sql(
            "UPDATE session SET teacher_id=NULL WHERE id=2"
        )
        await conn.exec_driver_sql("DELETE FROM user WHERE id=1")
        assert (
            await conn.exec_driver_sql(
                "SELECT owner_id FROM generation_run WHERE id='orphan-owner'"
            )
        ).scalar() is None
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []


async def database_state(conn):
    objects = (
        await conn.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    ).all()
    tables = [name for kind, name, _, _ in objects if kind == "table"]
    return dict(
        objects=objects,
        rows={
            name: (
                await conn.exec_driver_sql(
                    f'SELECT * FROM "{name}" ORDER BY rowid'
                )
            ).all()
            for name in tables
        },
        foreign_keys={
            name: (
                await conn.exec_driver_sql(f'PRAGMA foreign_key_list("{name}")')
            ).all()
            for name in tables
        },
    )


@pytest.fixture
async def populated036(tmp_path):
    engine = database(tmp_path / "upgrade.db")
    try:
        await migrate.run_all_migrations(through=36, db_engine=engine)
        async with engine.begin() as conn:
            await seed_classroom(conn)
            await conn.exec_driver_sql(
                "UPDATE provider_connection SET encrypted_key=?,nonce=?,encryption_key_version='v1',masked_hint='hint',enabled=1,credential_revision=1,updated_by=1,catalog_models_json='[\"original\"]' WHERE id=1",
                (b"k" * 17, b"n" * 12),
            )
            await conn.execute(
                ModelConfig.__table__.insert().values(
                    id=1,
                    provider_connection_id=1,
                    model_id="original",
                    display_name="Original",
                    enabled=True,
                )
            )
            await conn.execute(
                ProviderAuditLog.__table__.insert().values(
                    id=1,
                    actor_id=1,
                    provider_connection_id=1,
                    provider="openai",
                    change_kind="key_saved",
                    previous_credential_revision=0,
                    credential_revision=1,
                    previous_connection_version=1,
                    connection_version=1,
                )
            )
            await add_run(
                conn,
                "student",
                provider="openai",
                status="completed",
                partial_text="Original response",
                result_kind="message",
            )
            await add_run(
                conn,
                "analysis",
                provider="google",
                operation="analysis",
                status="ok",
                plan_json='{"chunks":1}',
                outcome_json='{"status":"ok"}',
                accepted_report_id=30,
                accepted_report_version=2,
            )
            await add_run(
                conn,
                "comparison-panel",
                provider="anthropic",
                session_id=None,
                comparison_run_id="comparison",
                panel_id="left",
            )
            await conn.execute(
                Message.__table__.insert().values(
                    id=10,
                    session_id=1,
                    role="student",
                    content="Original",
                    generation_run_id="student",
                )
            )
            await conn.execute(
                ApiUsageLog.__table__.insert().values(
                    id=20,
                    session_id=1,
                    run_id="student",
                    invocation_id="original-invocation",
                    attempt_no=1,
                    owner_id=1,
                    provider="openai",
                    total_tokens=12,
                    estimated_cost_usd=0.012345,
                    timestamp=NOW,
                    raw_usage_json={"tokens": 12},
                    context_budget_json={"limit": 100},
                    usage_scope="classroom",
                )
            )
            await conn.execute(
                SessionFeedbackReport.__table__.insert().values(
                    id=30,
                    session_id=1,
                    version=2,
                    model="original",
                    prompt_hash="prompt",
                    status="ok",
                    payload_json='{"feedback":"Original"}',
                    created_at=NOW,
                )
            )
            await conn.exec_driver_sql(
                "CREATE TABLE extra_trace(name TEXT NOT NULL)"
            )
            for table, column in zip(
                REBUILT, ("provider", "change_kind", "turn_id")
            ):
                await conn.exec_driver_sql(
                    f"CREATE INDEX extra_{table} ON {table}(lower({column})) WHERE {column} IS NOT NULL"
                )
                await conn.exec_driver_sql(
                    f"CREATE TRIGGER extra_watch_{table} AFTER UPDATE OF {column} ON {table} BEGIN INSERT INTO extra_trace VALUES('{table}'); END"
                )
        yield engine
    finally:
        await engine.dispose()


async def test_populated036_upgrade_preserves_all_rows_objects_and_matches_fresh(
    populated036, installed
):
    async with populated036.connect() as conn:
        before = await database_state(conn)
    await migrate.run_all_migrations(db_engine=populated036)
    async with populated036.connect() as conn:
        after = await database_state(conn)
        assert after["rows"]["demo_metadata"] == []
        for table, rows in before["rows"].items():
            current = after["rows"][table]
            assert (current[:-1] if table == "_migrations" else current) == rows
        assert after["rows"]["_migrations"][-1][1] == REVISION
        assert {
            name: fks
            for name, fks in after["foreign_keys"].items()
            if name != "demo_metadata"
        } == before["foreign_keys"]
        assert [
            row for row in after["objects"] if row[0] in ("index", "trigger")
        ] == [
            row for row in before["objects"] if row[0] in ("index", "trigger")
        ]
        assert (
            await conn.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
    await migrate.run_all_migrations(db_engine=populated036)
    async with populated036.begin() as conn:
        assert await database_state(conn) == after
        for table, column, row_id in zip(
            REBUILT, ("provider", "change_kind", "turn_id"), (1, 1, "student")
        ):
            await conn.exec_driver_sql(
                f"UPDATE {table} SET {column}={column} WHERE id=?", (row_id,)
            )
        assert (
            await conn.exec_driver_sql(
                "SELECT name FROM extra_trace ORDER BY name"
            )
        ).scalars().all() == sorted(REBUILT)
    async with installed.connect() as conn:
        fresh = await database_state(conn)
    assert [
        row for row in after["objects"] if not row[1].startswith("extra_")
    ] == fresh["objects"]


async def test_real037_sql_failure_rolls_back_every_row_object_and_history(
    populated036, tmp_path
):
    async with populated036.connect() as conn:
        before = await database_state(conn)
    failed = tmp_path / REVISION
    failed.write_text(
        (migrate.DIRECTORY / REVISION).read_text()
        + "\nINSERT INTO absent_table VALUES(1);"
    )
    with pytest.raises(OperationalError, match="absent_table"):
        await migrate.run_migration(failed, db_engine=populated036)
    async with populated036.connect() as conn:
        assert await database_state(conn) == before
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        assert (
            await conn.exec_driver_sql("PRAGMA integrity_check")
        ).scalar() == "ok"
        assert (
            await conn.exec_driver_sql("PRAGMA foreign_key_check")
        ).all() == []
    await migrate.run_all_migrations(db_engine=populated036)
    async with populated036.connect() as conn:
        assert await migrate._applied(conn, REVISION)


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_real031_receipt_and_artifacts_survive036_and037(data, tmp_path):
    from src.db.s2_cutover import rehearse

    await migrate.run_all_migrations(through=30, db_engine=data.engine)
    workspace = tmp_path / "rehearsal"
    report = await rehearse(
        Path(data.engine.url.database),
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    assert report["status"] == "ready"
    receipt = json.loads((workspace / "receipt.json").read_text())
    artifacts = {
        name: (workspace / name).read_bytes()
        for name in (*receipt["artifacts"], "receipt.json", "report.json")
    }
    engine = database(workspace / "rehearsal.db")
    try:
        async with engine.connect() as conn:
            history = (
                await conn.exec_driver_sql(
                    "SELECT * FROM _migrations ORDER BY id"
                )
            ).all()
            assert history[-1][1] == "031_scenario_contract.sql"
        await migrate.run_all_migrations(through=36, db_engine=engine)
        await migrate.run_all_migrations(db_engine=engine)
        async with engine.connect() as conn:
            after = await database_state(conn)
            assert after["rows"]["_migrations"][: len(history)] == history
            assert (
                await conn.exec_driver_sql("PRAGMA integrity_check")
            ).scalar() == "ok"
            assert (
                await conn.exec_driver_sql("PRAGMA foreign_key_check")
            ).all() == []
        await migrate.run_all_migrations(db_engine=engine)
        async with engine.connect() as conn:
            assert await database_state(conn) == after
        assert {
            name: (workspace / name).read_bytes() for name in artifacts
        } == artifacts
    finally:
        await engine.dispose()


def storage_type(column_type):
    # ORM JSON serializes as text; existing migrations declare its storage as TEXT.
    if isinstance(column_type, JSON):
        return "TEXT"
    declared = str(column_type).upper()
    if "INT" in declared:
        return "INTEGER"
    if any(part in declared for part in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in declared or not declared:
        return "BLOB"
    if any(part in declared for part in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


async def test_orm_columns_constraints_and_indexes_match_installed_ddl(
    installed,
):
    models = (DemoMetadata, ProviderConnection, ProviderAuditLog, GenerationRun)

    def compare(sync):
        inspector = inspect(sync)
        for model in models:
            table = model.__table__
            columns = inspector.get_columns(table.name)
            assert {column["name"] for column in columns} == set(
                table.columns.keys()
            )
            for reflected in columns:
                column = table.columns[reflected["name"]]
                assert storage_type(reflected["type"]) == storage_type(
                    column.type
                ), (table.name, column.name)
                implicit_integer_pk = (
                    reflected["primary_key"]
                    and str(reflected["type"]).upper() == "INTEGER"
                )
                assert (
                    reflected["nullable"] and not implicit_integer_pk
                ) == column.nullable, (table.name, column.name)
            expected_fks = {
                (
                    fk.parent.name,
                    fk.column.table.name,
                    fk.column.name,
                    fk.ondelete or "NO ACTION",
                )
                for fk in table.foreign_keys
            }
            actual_fks = {
                (
                    fk[3],
                    fk[2],
                    fk[4],
                    fk[6],
                )
                for fk in sync.exec_driver_sql(
                    f'PRAGMA foreign_key_list("{table.name}")'
                )
            }
            assert actual_fks == expected_fks
            expected_unique = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            assert {
                tuple(
                    row[2]
                    for row in sync.exec_driver_sql(
                        f'PRAGMA index_info("{index[1]}")'
                    )
                )
                for index in sync.exec_driver_sql(
                    f'PRAGMA index_list("{table.name}")'
                )
                if index[3] == "u"
            } == expected_unique
            actual_indexes = {
                index["name"]: index
                for index in inspector.get_indexes(table.name)
            }
            assert set(actual_indexes) == {
                index.name for index in table.indexes
            }
            for index in table.indexes:
                reflected = actual_indexes[index.name]
                assert tuple(reflected["column_names"]) == tuple(
                    column.name for column in index.columns
                )
                assert bool(reflected["unique"]) == index.unique
                where = index.dialect_options["sqlite"].get("where")
                expected_where = str(where) if where is not None else ""
                actual_where = str(
                    reflected["dialect_options"].get("sqlite_where", "")
                )
                assert " ".join(actual_where.split()) == " ".join(
                    expected_where.split()
                )

    async with installed.connect() as conn:
        await conn.run_sync(compare)
    async with async_sessionmaker(
        installed, expire_on_commit=False
    )() as session:
        connection = await session.get(ProviderConnection, 1)
        connection.catalog_models_json = [
            {"id": "stored-example", "roles": ["student"]}
        ]
        await session.commit()
        session.expunge_all()
        connection = (
            await session.execute(
                select(ProviderConnection).where(ProviderConnection.id == 1)
            )
        ).scalar_one()
        assert connection.catalog_models_json == [
            {"id": "stored-example", "roles": ["student"]}
        ]
    async with installed.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    "SELECT catalog_models_json FROM provider_connection WHERE id=1"
                )
            )
        ).scalar()
        assert json.loads(stored) == [
            {"id": "stored-example", "roles": ["student"]}
        ]

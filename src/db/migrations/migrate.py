"""Official SQLite install/upgrade entry point; historical SQL stays immutable."""

import argparse
import asyncio
import sqlite3
from pathlib import Path

from sqlalchemy import text

from src.db.connection import engine

BASELINE = "023_schema_baseline"
DIRECTORY = Path(__file__).parent


def statements(sql):
    """Split complete SQLite statements, including quoted semicolons/triggers."""
    pending = ""
    for char in sql:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip() and any(
        line.strip() and not line.lstrip().startswith("--")
        for line in pending.splitlines()
    ):
        raise ValueError("Migration ends with an incomplete SQL statement")


async def _history(conn):
    await conn.exec_driver_sql(
        """CREATE TABLE IF NOT EXISTS _migrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL UNIQUE,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )


async def _applied(conn, filename):
    return (
        await conn.execute(
            text("SELECT 1 FROM _migrations WHERE filename=:name"),
            {"name": filename},
        )
    ).scalar() is not None


async def _record(conn, filename):
    await conn.execute(
        text("INSERT INTO _migrations(filename) VALUES (:name)"),
        {"name": filename},
    )


async def run_migration(
    migration_file: Path, *, cutover_receipt=None, db_engine=None
):
    """DDL and history commit together, with an explicit SQLite transaction."""
    async with (db_engine or engine).connect() as conn:
        rebuild_runs = migration_file.name in (
            "028_generation_providers.sql",
            "029_scenario_config.sql",
            "031_scenario_contract.sql",
        )
        try:
            if rebuild_runs:
                # Dropping a referenced table with FKs enabled would mutate children.
                await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                await conn.commit()
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
            await _history(conn)
            if not await _applied(conn, migration_file.name):
                if migration_file.name == "031_scenario_contract.sql":
                    from src.db.s2_verification import verify_cleanup

                    await conn.run_sync(
                        lambda sync: verify_cleanup(sync, cutover_receipt)
                    )
                for statement in statements(migration_file.read_text()):
                    await conn.exec_driver_sql(statement)
                if (
                    rebuild_runs
                    and (
                        await conn.exec_driver_sql("PRAGMA foreign_key_check")
                    ).all()
                ):
                    raise ValueError(
                        "Foreign key violations during table rebuild"
                    )
                await _record(conn, migration_file.name)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            if rebuild_runs:
                # A failed commit can leave SQLite ahead of SQLAlchemy's state.
                # Closing rolls back the native transaction before restoring FKs.
                await conn.invalidate()
            raise
        finally:
            if rebuild_runs:
                await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                await conn.commit()


async def _install_baseline(*, db_engine=None):
    sql = (DIRECTORY / "baseline.sql").read_text()
    with sqlite3.connect(":memory:") as reference:
        reference.executescript(sql)
        tables = {
            name: [
                row[1]
                for row in reference.execute(f'PRAGMA table_info("{name}")')
            ]
            for (name,) in reference.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    async with (db_engine or engine).connect() as conn:
        # Rebuild is required to align NULL, FK actions, CHECKs and indexes.
        # Disable FKs before BEGIN, then validate every reference before commit.
        await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await conn.commit()
        try:
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
            await _history(conn)
            if await _applied(conn, BASELINE):
                await conn.commit()
                return
            existing = set(
                (
                    await conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ).scalars()
            )
            present = existing.intersection(tables)
            required = set(tables) - {"session_feedback_report", "ui_event"}
            if present and not required.issubset(present):
                raise ValueError(
                    "Unsupported schema: upgrade a backup to revision 018 first"
                )
            copied = {}
            for name in sorted(present):
                columns = [
                    row[1]
                    for row in (
                        await conn.exec_driver_sql(
                            f'PRAGMA table_info("{name}")'
                        )
                    )
                ]
                missing = set(tables[name]) - set(columns)
                allowed_missing = {
                    "api_usage_log": {"operation"},
                    "analysis_framework": {"category_name"},
                    "question_analysis": {"grade"},
                }.get(name, set())
                if (
                    set(columns) - set(tables[name])
                    or missing - allowed_missing
                ):
                    raise ValueError(
                        f"Unsupported columns in {name}; no changes committed"
                    )
                custom = (
                    await conn.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE tbl_name=:name AND type IN ('trigger', 'view')"
                        ),
                        {"name": name},
                    )
                ).first()
                if custom:
                    raise ValueError(
                        f"Custom schema object on {name}; migrate explicitly"
                    )
                copied[name] = ", ".join(f'"{column}"' for column in columns)
                await conn.exec_driver_sql(
                    f'CREATE TEMP TABLE "_copy_{name}" AS SELECT * FROM "{name}"'
                )
            for name in sorted(present):
                await conn.exec_driver_sql(f'DROP TABLE "{name}"')
            for statement in statements(sql):
                await conn.exec_driver_sql(statement)
            for name, columns in copied.items():
                await conn.exec_driver_sql(
                    f'INSERT INTO "{name}" ({columns}) SELECT {columns} FROM "_copy_{name}"'
                )
                await conn.exec_driver_sql(f'DROP TABLE "_copy_{name}"')
            violations = (
                await conn.exec_driver_sql("PRAGMA foreign_key_check")
            ).all()
            if violations:
                raise ValueError(f"Foreign key violations: {violations}")
            await _record(conn, BASELINE)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            await conn.commit()


async def run_all_migrations(
    *, through=None, cutover_receipt=None, db_engine=None
):
    await _install_baseline(db_engine=db_engine)
    # 001–022 are archived upgrade history, not fresh-install scripts.
    for path in sorted(DIRECTORY.glob("[0-9]*.sql")):
        revision = int(path.name.split("_")[0])
        if (
            revision > 23
            and (through is None or revision <= through)
            and not path.name.endswith("_down.sql")
        ):
            await run_migration(
                path, cutover_receipt=cutover_receipt, db_engine=db_engine
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", type=int, choices=range(23, 33))
    parser.add_argument("--cutover-receipt", type=Path)
    args = parser.parse_args()
    asyncio.run(
        run_all_migrations(
            through=args.through, cutover_receipt=args.cutover_receipt
        )
    )

"""Rehearse S2 on an explicit copy; no operating DB or environment defaults."""

import argparse
import asyncio
import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import URL, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.connection import set_sqlite_pragma
from src.db.convert_scenarios import convert_copy, private_artifact
from src.db.migrations import migrate
from src.db.s2_verification import (
    consistent_backup,
    database_state,
    ensure_idle,
    ensure_integrity,
    inventory,
    readonly,
    verify_artifacts,
    verify_preserved,
    verify_snapshots,
)
from src.services.legacy_conversion_values import EffectiveSettings


async def rehearse(source, workspace, settings, apply=False):
    source, workspace, settings = map(Path, (source, workspace, settings))
    if (
        not source.is_file()
        or source.is_symlink()
        or source.name == "dialogue_sim.db"
        or workspace.is_symlink()
        or source.resolve().is_relative_to(workspace.resolve())
        or settings.resolve().is_relative_to(workspace.resolve())
    ):
        raise ValueError("isolated_source_copy_required")
    effective = json.loads(settings.read_text())
    EffectiveSettings.model_validate(effective)
    workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
    if workspace.stat().st_mode & 0o077:
        raise ValueError("private_workspace_required")
    if any(workspace.iterdir()) and not (workspace / "effective.json").exists():
        raise ValueError("isolated_workspace_required")
    for path in workspace.iterdir():
        if (
            path.is_symlink()
            or path.stat().st_nlink != 1
            or path.stat().st_mode & 0o077
        ):
            raise ValueError("private_artifacts_required")
    backup, working = workspace / "backup.db", workspace / "rehearsal.db"
    consistent_backup(source, backup)
    if not working.exists():
        consistent_backup(backup, working)
    private_artifact(workspace / "effective.json", effective)
    report_path = workspace / "report.json"
    if report_path.exists():
        verify_artifacts(
            workspace, json.loads((workspace / "receipt.json").read_text())
        )
        report = json.loads(report_path.read_text())
        with readonly(working) as db:
            if inventory(db) != report["database"]:
                raise ValueError("completed_copy_changed")
        return report
    with readonly(working) as db:
        contracted = (
            db.execute(
                "SELECT 1 FROM _migrations WHERE filename='031_scenario_contract.sql'"
            ).fetchone()
            if "_migrations" in inventory(db)
            else None
        )
    if contracted:
        receipt = json.loads((workspace / "receipt.json").read_text())
        with readonly(working) as db, readonly(backup) as before:
            ensure_idle(db)
            ensure_integrity(db)
            preserved = verify_preserved(db, before)
            verify_snapshots(db)
            if inventory(db, contracted=True) != receipt["contracted"]:
                raise ValueError("completed_copy_changed")
            report = dict(
                status="ready",
                scenario_count=receipt["scenario_count"],
                restore_verified=True,
                results=receipt["results"],
                preserved=preserved,
                database=inventory(db),
            )
        verify_artifacts(workspace, receipt)
        private_artifact(report_path, report)
        return report
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(working))
    )
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_all_migrations(through=30, db_engine=engine)
    finally:
        await engine.dispose()
    archive, manifest = workspace / "archive.json", workspace / "manifest.json"
    await convert_copy(working, settings, archive, manifest)
    report = dict(
        status="dry_run",
        scenario_count=len(json.loads(manifest.read_text())["scenarios"]),
    )
    if not apply:
        return report
    with readonly(working) as db:
        ensure_idle(db)
        ensure_integrity(db)
    results = await convert_copy(
        working, settings, archive, manifest, apply=True
    )
    if any(row["status"] == "conflict" for row in results):
        raise ValueError("conversion_conflict")
    restore = workspace / "restore.db"
    consistent_backup(backup, restore)
    with readonly(backup) as before, readonly(restore) as restored:
        ensure_integrity(restored)
        if database_state(before) != database_state(restored):
            raise ValueError("restore_mismatch")
    with readonly(working) as db, readonly(backup) as before:
        preserved = verify_preserved(db, before)
        verify_snapshots(db)
        receipt = dict(
            artifacts={
                name: sha256((workspace / name).read_bytes()).hexdigest()
                for name in (
                    "backup.db",
                    "restore.db",
                    "archive.json",
                    "manifest.json",
                    "effective.json",
                )
            },
            preserved=preserved,
            database=inventory(db),
            contracted=inventory(db, contracted=True),
            results=results,
            scenario_count=report["scenario_count"],
        )
    receipt_path = workspace / "receipt.json"
    if receipt_path.exists():
        # A resumed conversion reports skipped; retain the original audit outcome.
        results = json.loads(receipt_path.read_text())["results"]
        receipt["results"] = results
    private_artifact(receipt_path, receipt)
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(working))
    )
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_migration(
            migrate.DIRECTORY / "031_scenario_contract.sql",
            cutover_receipt=receipt_path,
            db_engine=engine,
        )
    finally:
        await engine.dispose()
    with readonly(working) as db, readonly(backup) as before:
        ensure_integrity(db)
        verify_preserved(db, before)
        verify_snapshots(db)
        report.update(
            status="ready",
            restore_verified=True,
            results=results,
            preserved=preserved,
            database=inventory(db),
        )
    private_artifact(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-copy", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="convert and contract the rehearsal copy only",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(
            rehearse(
                args.source_copy, args.workspace, args.settings, args.apply
            )
        )
    except (
        ValueError,
        KeyError,
        OSError,
        sqlite3.Error,
        SQLAlchemyError,
        HTTPException,
    ) as error:
        code = str(error)
        if code not in {
            "active_sessions",
            "unfinished_runs",
            "backup_mismatch",
            "manifest_mismatch",
            "artifact_mismatch",
            "receipt_mismatch",
            "preservation_mismatch",
            "restore_mismatch",
            "conversion_conflict",
            "completed_copy_changed",
            "isolated_source_copy_required",
            "isolated_workspace_required",
            "private_workspace_required",
            "private_artifacts_required",
            "database_integrity",
        }:
            code = "invalid_input_or_evidence"
        parser.exit(
            2, f"S2 rehearsal refused: {code}; inspect private artifacts.\n"
        )
    print(json.dumps(report))


if __name__ == "__main__":
    main()

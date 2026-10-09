"""Preservation evidence required before destructive S2 schema cleanup."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from src.api.schemas.scenario_config import ScenarioConfig
from src.services.scenario_conversion import checksum
from src.services.session_history import session_display


@contextmanager
def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def inventory(db, *, contracted=False):
    """Private hashes cover all rows, including NULLs, without exporting secrets."""
    result = {}
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        if contracted and name in {
            "prompt_template",
            "analysis_framework",
            "_migrations",
        }:
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [r[1] for r in db.execute(f"PRAGMA table_info({quoted})")]
        if contracted and name == "scenario":
            from src.models import Scenario

            columns = [
                key for key in columns if key in Scenario.__table__.columns
            ]
        columns.sort()
        projection = ",".join(
            '"' + key.replace('"', '""') + '"' for key in columns
        )
        rows = db.execute(
            f"SELECT {projection} FROM {quoted} ORDER BY rowid"
        ).fetchall()
        result[name] = dict(
            columns=columns, count=len(rows), hash=row_hash(rows)
        )
    return result


def database_state(db):
    return dict(
        tables=inventory(db),
        schema=db.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall(),
    )


def consistent_backup(source, destination):
    """Include committed WAL pages; never replace an earlier different backup."""
    with readonly(source) as db:
        if destination.exists():
            with readonly(destination) as prior:
                if database_state(prior) != database_state(db):
                    raise ValueError("backup_mismatch")
            return
        os.close(
            os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        )
        with sqlite3.connect(destination) as backup:
            db.backup(backup)


def row_hash(rows):
    return checksum(
        [
            [
                {"blob": value.hex()} if isinstance(value, bytes) else value
                for value in row
            ]
            for row in rows
        ]
    )


def query(db, sql):
    if hasattr(db, "exec_driver_sql"):
        return db.exec_driver_sql(sql).fetchall()
    return db.execute(sql).fetchall()


def ensure_idle(db):
    if query(db, "SELECT id FROM session WHERE ended_at IS NULL"):
        raise ValueError("active_sessions")
    for table, state in (
        ("generation_run", "running"),
        ("model_probe", "verifying"),
    ):
        if query(
            db,
            f"SELECT 1 FROM {table} WHERE status='{state}' OR (started_at IS NOT NULL AND finished_at IS NULL)",
        ):
            raise ValueError("unfinished_runs")
    if query(
        db,
        "SELECT 1 FROM api_usage_log WHERE finished_at IS NULL AND (status='running' OR started_at IS NOT NULL)",
    ):
        raise ValueError("unfinished_runs")


def ensure_integrity(db):
    if query(db, "PRAGMA integrity_check") != [("ok",)] or query(
        db, "PRAGMA foreign_key_check"
    ):
        raise ValueError("database_integrity")


def verify_preserved(db, backup):
    """Compare original columns; only conversion fields/history may be added."""
    before = inventory(backup)
    evidence = {}
    for table, info in before.items():
        if table in {
            "_migrations",
            "scenario",
            "prompt_template",
            "analysis_framework",
        }:
            continue
        columns = info["columns"]
        if table == "session":
            columns = [
                key
                for key in columns
                if key
                not in {
                    "config_snapshot_json",
                    "config_hash",
                    "source_scenario_version",
                    "snapshot_origin",
                    "snapshot_created_at",
                }
            ]
        quoted = '"' + table.replace('"', '""') + '"'
        projection = ",".join(
            '"' + column.replace('"', '""') + '"' for column in columns
        )
        sql = f"SELECT {projection} FROM {quoted} ORDER BY rowid"
        rows = query(db, sql)
        if rows != query(backup, sql):
            raise ValueError("preservation_mismatch")
        evidence[table] = dict(
            columns=columns, count=len(rows), hash=row_hash(rows)
        )
    if "snapshot_origin" in before.get("session", {}).get("columns", []):
        sql = "SELECT id,config_snapshot_json,config_hash,source_scenario_version,snapshot_origin,snapshot_created_at FROM session WHERE snapshot_origin IS NOT NULL ORDER BY id"
        original = query(backup, sql)
        current = {row[0]: tuple(row) for row in query(db, sql)}
        if any(current.get(row[0]) != tuple(row) for row in original):
            raise ValueError("snapshot_preservation_mismatch")
    columns = "id,is_active,deleted_at,created_by,created_at"
    if query(db, f"SELECT {columns} FROM scenario ORDER BY id") != query(
        backup, f"SELECT {columns} FROM scenario ORDER BY id"
    ):
        raise ValueError("scenario_identity_mismatch")
    return evidence


def verify_snapshots(db):
    for (config,) in query(db, "SELECT config_json FROM scenario"):
        values = json.loads(config) if config else None
        if ScenarioConfig.model_validate(values).model_dump() != values:
            raise ValueError("invalid_config")
    for row in query(
        db,
        "SELECT snapshot_origin,config_snapshot_json,config_hash,source_scenario_version,snapshot_created_at FROM session",
    ):
        from datetime import datetime

        origin, config, config_hash, version, created_at = row
        if origin not in {"native", "legacy_reconstructed"}:
            raise ValueError("unconverted_session")
        session_display(
            SimpleNamespace(
                snapshot_origin=origin,
                config_snapshot_json=json.loads(config),
                config_hash=config_hash,
                source_scenario_version=version,
                snapshot_created_at=(
                    datetime.fromisoformat(created_at) if created_at else None
                ),
            )
        )


def verify_cleanup(db, receipt_path):
    """Called inside the migration writer transaction before any DROP."""
    ensure_idle(db)
    ensure_integrity(db)
    if (
        not query(db, "SELECT id FROM scenario")
        and not query(db, "SELECT id FROM prompt_template")
        and not query(db, "SELECT id FROM analysis_framework")
    ):
        return  # Empty installs have no legacy source to preserve.
    if receipt_path is None:
        raise ValueError("validated_cutover_required")
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    root = receipt_path.parent
    verify_artifacts(root, receipt)
    with readonly(root / "backup.db") as backup:
        if verify_preserved(db, backup) != receipt["preserved"]:
            raise ValueError("receipt_mismatch")
    # Hash every row again within the locked transaction, including config/review.
    for table, info in receipt["database"].items():
        quoted = '"' + table.replace('"', '""') + '"'
        projection = ",".join(
            '"' + key.replace('"', '""') + '"' for key in info["columns"]
        )
        current = query(db, f"SELECT {projection} FROM {quoted} ORDER BY rowid")
        if len(current) != info["count"] or row_hash(current) != info["hash"]:
            raise ValueError("receipt_mismatch")
    verify_snapshots(db)


def verify_artifacts(root, receipt):
    from hashlib import sha256

    if set(receipt["artifacts"]) != {
        "backup.db",
        "restore.db",
        "archive.json",
        "manifest.json",
        "effective.json",
    }:
        raise ValueError("artifact_mismatch")
    for name, digest in receipt["artifacts"].items():
        if sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError("artifact_mismatch")
    with (
        readonly(root / "backup.db") as before,
        readonly(root / "restore.db") as restored,
    ):
        if database_state(before) != database_state(restored):
            raise ValueError("restore_mismatch")

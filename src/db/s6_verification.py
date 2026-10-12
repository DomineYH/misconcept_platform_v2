"""Current-format S6 schema, preservation and restoration checks."""

import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.db.s2_verification import (
    consistent_backup,
    database_state,
    ensure_idle,
    ensure_integrity,
    inventory,
    readonly,
    row_hash,
)

ARTIFACTS = {
    "backup.db",
    "candidate.db",
    "restore-source.db",
    "restore-candidate.db",
    "dry-run.json",
    "applying.json",
    "report.json",
    "receipt.json",
    "failure.json",
}
ARTIFACTS |= {
    name + suffix
    for name in tuple(ARTIFACTS)
    if name.endswith(".db")
    for suffix in ("-wal", "-shm")
}


def private_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("private_artifacts_required")
    info = path.stat()
    if info.st_nlink != 1 or info.st_mode & 0o077 or not info.st_mode & 0o400:
        raise ValueError("private_artifacts_required")


def validate_paths(
    source,
    workspace,
    *,
    artifacts=ARTIFACTS,
    markers=("dry-run.json", "report.json"),
):
    for path in (source, workspace):
        if any(
            part.is_symlink()
            for part in (path.absolute(), *path.absolute().parents)
        ):
            raise ValueError("isolated_source_copy_required")
    if (
        not source.is_file()
        or source.name in {"dialogue_sim.db", ".env"}
        or source.resolve().is_relative_to(workspace.resolve())
    ):
        raise ValueError("isolated_source_copy_required")
    private_file(source)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private_file(sidecar)
    workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
    if (
        workspace.stat().st_mode & 0o077
        or workspace.stat().st_mode & 0o700 != 0o700
    ):
        raise ValueError("private_workspace_required")
    files = list(workspace.iterdir())
    for path in files:
        private_file(path)
    if any(path.name not in artifacts for path in files) or (
        files and not any((workspace / name).is_file() for name in markers)
    ):
        raise ValueError("isolated_workspace_required")


async def upgrade(path, through=35):
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path))
    )
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        await migrate.run_all_migrations(through=through, db_engine=engine)
    finally:
        await engine.dispose()


async def official_schema(workspace, revision):
    with tempfile.TemporaryDirectory(dir=workspace) as directory:
        reference = Path(directory) / "reference.db"
        with sqlite3.connect(reference):
            pass
        reference.chmod(0o600)
        if revision:
            await upgrade(reference, revision)
        with readonly(reference) as db:
            return database_state(db)["schema"]


def restore(source, destination):
    consistent_backup(source, destination)
    with readonly(source) as original, readonly(destination) as restored:
        ensure_integrity(restored)
        if database_state(original) != database_state(restored):
            raise ValueError("restore_mismatch")


def source_revision(db, *, allow_legacy=False):
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if not tables:
        return 0
    if ("_migrations",) not in tables:
        raise ValueError("unsupported_history")
    names = [
        row[0]
        for row in db.execute("SELECT filename FROM _migrations ORDER BY id")
    ]
    official = [
        path.name
        for path in sorted(migrate.DIRECTORY.glob("[0-9]*.sql"))
        if not path.name.endswith("_down.sql")
    ]
    if migrate.BASELINE not in names or set(names) - {
        migrate.BASELINE,
        *official,
    }:
        raise ValueError("unsupported_history")
    forward = [
        name for name in names if name == migrate.BASELINE or int(name[:3]) > 23
    ]
    expected = [migrate.BASELINE] + [
        name for name in official if int(name[:3]) > 23
    ]
    if forward != expected[: len(forward)]:
        raise ValueError("unsupported_history")
    revision = int(forward[-1][:3])
    if revision < 31 and not allow_legacy:
        raise ValueError("legacy_path_required")
    return revision


def idle(db):
    ensure_idle(db)
    if (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='comparison_run'"
        ).fetchone()
        and db.execute(
            "SELECT 1 FROM comparison_run WHERE status='running'"
        ).fetchone()
    ):
        raise ValueError("unfinished_runs")


def preserved(db, backup):
    """Every original column stays exact; history only appends official rows."""
    evidence = {}
    for table, info in inventory(backup).items():
        fields = ",".join(
            '"' + column.replace('"', '""') + '"' for column in info["columns"]
        )
        quoted = '"' + table.replace('"', '""') + '"'
        rows = db.execute(
            f"SELECT {fields} FROM {quoted} ORDER BY rowid"
        ).fetchall()
        if table == "_migrations":
            rows = rows[: info["count"]]
        current = dict(
            columns=info["columns"], count=len(rows), hash=row_hash(rows)
        )
        if current != info:
            raise ValueError("preservation_mismatch")
        for column in db.execute(f"PRAGMA table_info({quoted})"):
            if column[1] not in info["columns"]:
                field = '"' + column[1].replace('"', '""') + '"'
                if (
                    column[3]
                    or db.execute(
                        f"SELECT 1 FROM {quoted} WHERE {field} IS NOT NULL LIMIT 1"
                    ).fetchone()
                ):
                    raise ValueError("unexpected_added_value")
        evidence[table] = current
    return evidence

"""Prepare S6 evidence on explicit isolated SQLite copies."""

import argparse
import asyncio
import json
import subprocess
from hashlib import sha256
from pathlib import Path

from src.db.convert_scenarios import private_artifact
from src.db.migrations import migrate
from src.db.s2_verification import (
    consistent_backup,
    database_state,
    ensure_integrity,
    readonly,
    verify_snapshots,
)
from src.db.s6_verification import (
    idle,
    official_schema,
    preserved,
    restore,
    source_revision,
    upgrade,
    validate_paths,
)
from src.services.lesson_snapshots import canonical_hash

SEALED = (
    "backup.db",
    "candidate.db",
    "restore-source.db",
    "restore-candidate.db",
    "dry-run.json",
    "applying.json",
)
ERROR_CODES = {
    "isolated_source_copy_required",
    "isolated_workspace_required",
    "private_workspace_required",
    "private_artifacts_required",
    "unsupported_history",
    "unsupported_schema",
    "legacy_path_required",
    "active_sessions",
    "unfinished_runs",
    "database_integrity",
    "backup_mismatch",
    "restore_mismatch",
    "preservation_mismatch",
    "unexpected_added_value",
    "artifact_mismatch",
    "manifest_mismatch",
    "report_mismatch",
    "schema_mismatch",
    "completed_copy_changed",
    "migration_not_idempotent",
    "interrupted_workspace",
    "incomplete_evidence",
    "legacy_arguments_required",
    "conversion_conflict",
    "receipt_mismatch",
}


def failure(error):
    code = str(error)
    return dict(
        status="failed",
        release_blocked=True,
        error_code=code if code in ERROR_CODES else "invalid_input_or_evidence",
    )


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def code_identity():
    root = Path(__file__).resolve().parents[2]
    files = [
        Path(__file__),
        Path(__file__).with_name("s6_verification.py"),
        Path(__file__).with_name("s2_verification.py"),
        Path(__file__).with_name("s2_cutover.py"),
        Path(__file__).with_name("s6_legacy.py"),
        Path(__file__).with_name("convert_scenarios.py"),
        migrate.DIRECTORY / "migrate.py",
        migrate.DIRECTORY / "baseline.sql",
        *sorted(migrate.DIRECTORY.glob("[0-9]*.sql")),
    ]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    return dict(
        code_revision=revision,
        tool_hash=canonical_hash(
            {str(path.relative_to(root)): digest(path) for path in files}
        ),
    )


def receipt(workspace):
    return dict(
        report_hash=digest(workspace / "report.json"),
        artifacts={name: digest(workspace / name) for name in SEALED},
    )


async def completed_report(workspace, base):
    candidate, backup = workspace / "candidate.db", workspace / "backup.db"
    with readonly(candidate) as db, readonly(backup) as original:
        ensure_integrity(db)
        if source_revision(db) != 36:
            raise ValueError("completed_copy_changed")
        idle(db)
        verify_snapshots(db)
        evidence = preserved(db, original)
        state = database_state(db)
    if state["schema"] != await official_schema(workspace, 36):
        raise ValueError("schema_mismatch")
    restore(backup, workspace / "restore-source.db")
    restore(candidate, workspace / "restore-candidate.db")
    return dict(
        **base,
        status="ready",
        candidate_revision=36,
        database=state["tables"],
        schema_hash=canonical_hash(state["schema"]),
        preserved=evidence,
        reader_checks="synthetic_ci_only",
        credential_restore="separate_gate",
        checks=dict(
            integrity=True,
            foreign_keys=True,
            preserved=True,
            restore_source=True,
            restore_candidate=True,
            migration_idempotence=True,
            fresh_schema=True,
        ),
    )


async def rehearse(
    source, workspace, apply=False, *, settings=None, legacy_workspace=None
):
    source, workspace = map(Path, (source, workspace))
    validate_paths(source, workspace)
    try:
        if settings is not None or legacy_workspace is not None:
            if settings is None or legacy_workspace is None:
                raise ValueError("legacy_arguments_required")
            from src.db.s6_legacy import rehearse_legacy

            return await rehearse_legacy(
                source, workspace, Path(settings), Path(legacy_workspace), apply
            )
        return await _rehearse(source, workspace, apply)
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as error:
        try:
            private_artifact(workspace / "failure.json", failure(error))
        except (OSError, ValueError):
            pass  # Keep an earlier failure or an interrupted write immutable.
        raise


async def _rehearse(source, workspace, apply, legacy=None):
    complete = (workspace / "report.json").exists()
    if (workspace / "applying.json").exists() and not complete:
        raise ValueError("interrupted_workspace")
    if complete:
        if any(
            not (workspace / name).is_file()
            for name in (*SEALED, "receipt.json")
        ):
            raise ValueError("incomplete_evidence")
        if json.loads((workspace / "receipt.json").read_text()) != receipt(
            workspace
        ):
            raise ValueError("artifact_mismatch")
    elif (workspace / "dry-run.json").exists() and any(
        not (workspace / name).is_file()
        for name in ("backup.db", "candidate.db")
    ):
        raise ValueError("incomplete_evidence")
    backup, candidate = workspace / "backup.db", workspace / "candidate.db"
    consistent_backup(source, backup)
    with readonly(backup) as db:
        revision = source_revision(db)
        ensure_integrity(db)
        source_state = database_state(db)
    if source_state["schema"] != await official_schema(workspace, revision):
        raise ValueError("unsupported_schema")
    if revision:
        with readonly(backup) as db:
            idle(db)
            verify_snapshots(db)
    base = dict(
        **code_identity(),
        release_blocked=True,
        source_revision=revision,
        source_state_hash=canonical_hash(source_state),
    )
    if legacy is not None:
        base["legacy"] = legacy
    if complete:
        report = await completed_report(workspace, base)
        if json.loads((workspace / "report.json").read_text()) != report:
            raise ValueError("report_mismatch")
        return report
    consistent_backup(backup, candidate)
    dry_run = dict(
        **base,
        status="dry_run",
        candidate_revision=revision,
        candidate_hash=digest(candidate),
        backup_hash=digest(backup),
    )
    private_artifact(workspace / "dry-run.json", dry_run)
    if not apply:
        return dry_run
    private_artifact(workspace / "applying.json", base)
    await upgrade(candidate)
    with readonly(candidate) as db:
        state = database_state(db)
    await upgrade(candidate)
    with readonly(candidate) as db:
        if database_state(db) != state:
            raise ValueError("migration_not_idempotent")
    report = await completed_report(workspace, base)
    consistent_backup(source, backup)
    private_artifact(workspace / "report.json", report)
    private_artifact(workspace / "receipt.json", receipt(workspace))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-copy", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--legacy-workspace", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="upgrade only the isolated candidate (default: dry-run)",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(
            rehearse(
                args.source_copy,
                args.workspace,
                args.apply,
                settings=args.settings,
                legacy_workspace=args.legacy_workspace,
            )
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as error:
        print(json.dumps(failure(error)))
        parser.exit(
            2,
            "S6 rehearsal refused; retain evidence and retry in a new private workspace.\n",
        )
    print(json.dumps(report))


if __name__ == "__main__":
    main()

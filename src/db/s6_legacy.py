"""Connect the unchanged S2 receipt flow to a separate latest S6 candidate."""

from src.db.s2_cutover import rehearse as rehearse_s2
from src.db.s2_verification import (
    consistent_backup,
    database_state,
    ensure_idle,
    ensure_integrity,
    readonly,
)
from src.db.s6_verification import (
    official_schema,
    private_file,
    source_revision,
    validate_paths,
)
from src.services.lesson_snapshots import canonical_hash

S2_REQUIRED = {
    "backup.db",
    "rehearsal.db",
    "restore.db",
    "effective.json",
    "archive.json",
    "manifest.json",
    "receipt.json",
    "report.json",
}
S2_ARTIFACTS = S2_REQUIRED | {
    name + suffix
    for name in S2_REQUIRED
    if name.endswith(".db")
    for suffix in ("-wal", "-shm")
}


async def rehearse_legacy(source, workspace, settings, legacy_workspace, apply):
    from src.db.s6_cutover import _rehearse, code_identity, digest

    if (
        workspace.resolve().is_relative_to(legacy_workspace.resolve())
        or legacy_workspace.resolve().is_relative_to(workspace.resolve())
        or settings.name in {".env", "dialogue_sim.db"}
        or any(
            path.is_symlink()
            for path in (settings.absolute(), *settings.absolute().parents)
        )
        or any(
            settings.resolve().is_relative_to(directory.resolve())
            for directory in (workspace, legacy_workspace)
        )
    ):
        raise ValueError("isolated_workspace_required")
    private_file(settings)
    validate_paths(
        source,
        legacy_workspace,
        artifacts=S2_ARTIFACTS,
        markers=("effective.json",),
    )
    if any(
        (workspace / name).exists() for name in ("dry-run.json", "report.json")
    ) and any(not (legacy_workspace / name).is_file() for name in S2_REQUIRED):
        raise ValueError("incomplete_evidence")
    with readonly(source) as db:
        revision = source_revision(db, allow_legacy=True)
        ensure_integrity(db)
        state = database_state(db)
    if revision not in range(23, 31):
        raise ValueError("legacy_path_required")
    if state["schema"] != await official_schema(workspace, revision):
        raise ValueError("unsupported_schema")
    s2 = await rehearse_s2(source, legacy_workspace, settings, apply=apply)
    with readonly(legacy_workspace / "rehearsal.db") as db:
        ensure_idle(db)
        ensure_integrity(db)
    consistent_backup(source, legacy_workspace / "backup.db")
    legacy = dict(
        source_revision=revision,
        source_state_hash=canonical_hash(state),
        settings_hash=digest(legacy_workspace / "effective.json"),
    )
    if s2["status"] != "ready":
        return dict(
            **code_identity(),
            status="dry_run",
            release_blocked=True,
            legacy=legacy,
            stage="s2_conversion_plan",
            scenario_count=s2["scenario_count"],
        )
    legacy.update(
        receipt_hash=digest(legacy_workspace / "receipt.json"),
        report_hash=digest(legacy_workspace / "report.json"),
        preserved=s2["preserved"],
    )
    report = await _rehearse(
        legacy_workspace / "rehearsal.db", workspace, apply, legacy=legacy
    )
    consistent_backup(source, legacy_workspace / "backup.db")
    return report

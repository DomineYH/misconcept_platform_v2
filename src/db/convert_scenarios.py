"""Dry-run and apply #61 candidates on an explicitly selected consistent copy."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.scenario_conversion import (
    apply_manifest,
    capture_archive,
    conversion_manifest,
)


def private_artifact(path, value):
    """Create immutable private artifacts, or verify an identical earlier run."""
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError("manifest_mismatch")
        return
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            value,
            file,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")


async def convert_copy(
    database_copy, settings_path, archive_path, manifest_path, apply=False
):
    if not database_copy.is_file() or database_copy.name == "dialogue_sim.db":
        raise ValueError("consistent_database_copy_required")
    effective = json.loads(settings_path.read_text())
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(database_copy))
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            # A real BEGIN makes all archive reads share the same SQLite snapshot.
            await db.execute(text("BEGIN"))
            archive = (
                json.loads(archive_path.read_text())
                if archive_path.exists()
                else await capture_archive(db)
            )
            manifest = await conversion_manifest(
                db, archive, effective, str(archive_path.resolve())
            )
            private_artifact(archive_path, archive)
            private_artifact(manifest_path, manifest)
            if not apply:
                return []
            results = await apply_manifest(db, archive, manifest)
            await db.commit()
            return results
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database-copy", "settings", "archive", "manifest"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write candidates to the selected copy (default: dry run)",
    )
    args = parser.parse_args()
    try:
        results = asyncio.run(
            convert_copy(
                args.database_copy,
                args.settings,
                args.archive,
                args.manifest,
                args.apply,
            )
        )
    except (ValueError, KeyError):
        parser.exit(
            2,
            "Conversion refused: invalid input or artifact mismatch. No input values are printed.\n",
        )
    print(
        json.dumps(
            dict(
                mode="apply" if args.apply else "dry_run",
                manifest=str(args.manifest),
                scenario_count=len(
                    json.loads(args.manifest.read_text())["scenarios"]
                ),
                results=results,
            )
        )
    )
    if any(row["status"] == "conflict" for row in results):
        parser.exit(
            2, "Conflicts were preserved; inspect the private manifest.\n"
        )


if __name__ == "__main__":
    main()

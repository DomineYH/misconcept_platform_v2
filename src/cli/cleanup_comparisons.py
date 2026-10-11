"""Explicit external retention command; never create or migrate a database."""

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.comparison_retention import cleanup_comparisons


async def run(database, execute):
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=database.resolve().as_uri(),
            query={"uri": "true", "mode": "rw" if execute else "ro"},
        )
    )

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        return await cleanup_comparisons(
            async_sessionmaker(engine, expire_on_commit=False), execute=execute
        )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        counts = asyncio.run(run(args.database, args.execute))
    except Exception:
        counts = dict(targets=0, deleted=0, failed=1, skipped_running=0)
    exit_status = int(counts["failed"] > 0)
    print(
        json.dumps(
            dict(
                mode="execute" if args.execute else "dry-run",
                **counts,
                completed_at=datetime.now(timezone.utc).isoformat(),
                exit_status=exit_status,
            )
        )
    )
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())

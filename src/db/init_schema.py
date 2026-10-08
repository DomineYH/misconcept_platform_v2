"""Compatibility entry point for the official schema installer."""

import asyncio

from src.db.migrations.migrate import run_all_migrations

init_schema = run_all_migrations

if __name__ == "__main__":
    asyncio.run(init_schema())

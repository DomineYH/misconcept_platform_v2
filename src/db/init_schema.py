"""Compatibility entry point for the official schema installer."""

# ruff: noqa: E402
import json
import os

# CLI preflight precedes imports that can initialize configuration or engines.
if __name__ == "__main__" and os.environ.get("APP_MODE", "normal") != "normal":
    mode = os.environ.get("APP_MODE")
    code = (
        "DEMO_ACTION_FORBIDDEN"
        if mode == "demo"
        else "DEMO_CONFIGURATION_INVALID"
    )
    print(json.dumps(dict(code=code, exit_status=1)))
    raise SystemExit(1)


import asyncio

from src.db.migrations.migrate import run_all_migrations

init_schema = run_all_migrations

if __name__ == "__main__":
    asyncio.run(init_schema())

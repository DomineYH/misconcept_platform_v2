"""Explicit approved provider smoke against an isolated synthetic database."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from src.cli.cleanup_comparisons import require_normal


async def run(
    database,
    selections,
    *,
    owner_id,
    plan_id="initial",
    execute=False,
    approval=None,
    output=None,
    app_mode=None,
):
    require_normal(
        os.environ.get("APP_MODE", "normal") if app_mode is None else app_mode
    )
    if execute and approval is None:
        raise ValueError("approval_required")
    if os.environ.get("TESTING", "").lower() != "true":
        raise ValueError("isolated_environment_required")
    if any(
        name in os.environ
        for name in (
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
            "GOOGLE_GEMINI_BASE_URL",
            "GOOGLE_VERTEX_BASE_URL",
        )
    ):
        raise ValueError("native_environment_required")
    from sqlalchemy import URL
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.config import config
    from src.services.provider_smoke_plan import build_plan, digest

    database = Path(database).resolve()
    runtime_url = make_url(config.DATABASE_URL)
    runtime_database = (
        Path(runtime_url.database.removeprefix("file:")).resolve()
        if runtime_url.get_backend_name() == "sqlite"
        and runtime_url.database not in (None, ":memory:")
        else None
    )
    if (
        database == runtime_database
        or database.name == "dialogue_sim.db"
        or not database.is_file()
    ):
        raise ValueError("isolated_database_required")
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=database.as_uri(),
            query={"uri": "true", "mode": "rw" if execute else "ro"},
        )
    )
    try:
        plan = await build_plan(
            async_sessionmaker(engine, expire_on_commit=False),
            selections,
            owner_id,
            plan_id,
        )
        report = dict(
            schema_version=1,
            mode="dry-run",
            plan=plan,
            plan_sha256=digest(plan),
            samples=[],
        )
        if execute:
            from src.services.provider_smoke_execution import execute_plan

            return await execute_plan(
                async_sessionmaker(engine, expire_on_commit=False),
                report,
                approval,
                Path(output) if output else None,
            )
        return report
    finally:
        await engine.dispose()


def main(*, app_mode=None):
    app_mode = (
        os.environ.get("APP_MODE", "normal") if app_mode is None else app_mode
    )
    try:
        require_normal(app_mode)
    except ValueError as error:
        print(json.dumps(dict(code=str(error), exit_status=1)))
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--plan-id", default="initial")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(
            run(
                args.database,
                json.loads(args.selection.read_text()),
                owner_id=args.owner_id,
                plan_id=args.plan_id,
                execute=args.execute,
                approval=(
                    json.loads(args.approval.read_text())
                    if args.approval
                    else None
                ),
                output=args.output,
                app_mode=app_mode,
            )
        )
    except Exception as error:
        code = getattr(error, "code", None) or str(error)
        allowed = {
            "approval_required",
            "approval_mismatch",
            "output_required",
            "isolated_environment_required",
            "native_environment_required",
            "isolated_database_required",
            "classroom_database_forbidden",
            "configuration_unavailable",
            "budget_exceeded",
            "unknown_bound",
            "unknown_price",
            "duplicate_role",
            "invalid_plan",
        }
        print(
            json.dumps(
                dict(
                    status="refused",
                    error_code=code if code in allowed else "plan_refused",
                )
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return int(report.get("status") == "failed")


if __name__ == "__main__":
    raise SystemExit(main())

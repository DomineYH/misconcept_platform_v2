"""Live cancellation signals for the supported single async worker."""

from datetime import datetime, timezone

from sqlalchemy import select, text, update

from src.demo.lifecycle import NormalContext, lifecycle
from src.models import ApiUsageLog, ComparisonRun, GenerationRun

# DB status owns execution rights; these signals only stop upstream reads.
active_runs = lifecycle.normal_view("run", lambda event: event.set())


async def interrupt_orphans(factory, *, ctx=None, registry=lifecycle):
    """Run before serving traffic; never resume or regenerate prior work."""
    ctx = ctx if ctx is not None else NormalContext(factory)
    factory = ctx.factory
    async with registry.write_guard(ctx), factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        await db.execute(
            update(ApiUsageLog)
            .where(
                ApiUsageLog.run_id.in_(
                    select(GenerationRun.id).where(
                        GenerationRun.comparison_run_id.is_not(None)
                    )
                ),
                ApiUsageLog.status == "running",
            )
            .values(
                status="interrupted",
                error_code="server_restarted",
                finished_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(
            update(GenerationRun)
            .where(GenerationRun.status == "running")
            .values(
                status="interrupted",
                error_code="server_restarted",
                finished_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(
            update(ComparisonRun)
            .where(ComparisonRun.status == "running")
            .values(status="finished")
        )
        try:
            await db.commit()
        except BaseException:
            await db.invalidate()
            raise

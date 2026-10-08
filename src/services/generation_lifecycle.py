"""Live cancellation signals for the supported single async worker."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text, update

from src.models import GenerationRun

# DB status owns execution rights; these signals only stop upstream reads.
active_runs: dict[str, asyncio.Event] = {}


async def interrupt_orphans(factory):
    """Run before serving traffic; never resume or regenerate prior work."""
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        await db.execute(
            update(GenerationRun)
            .where(GenerationRun.status == "running")
            .values(
                status="interrupted",
                error_code="server_restarted",
                finished_at=datetime.now(timezone.utc),
            )
        )
        try:
            await db.commit()
        except BaseException:
            await db.invalidate()
            raise

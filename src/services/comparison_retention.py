"""Bounded retention batches; usage references and content change atomically."""

import logging

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.models import ComparisonRun
from src.services.comparison_history import delete_contents, running_content
from src.services.comparison_state import utcnow

logger = logging.getLogger(__name__)


async def cleanup_comparisons(factory, *, execute=False):
    expired_or_ownerless = or_(
        ComparisonRun.expires_at <= utcnow(), ComparisonRun.admin_id.is_(None)
    )
    eligible = expired_or_ownerless & ~running_content()
    counts = dict(targets=0, deleted=0, failed=0, skipped_running=0)
    async with factory() as db:
        counts["targets"] = await db.scalar(
            select(func.count()).select_from(ComparisonRun).where(eligible)
        )
        counts["skipped_running"] = await db.scalar(
            select(func.count())
            .select_from(ComparisonRun)
            .where(expired_or_ownerless, running_content())
        )
    if not execute:
        return counts
    while True:
        async with factory() as db:
            ids = []
            try:
                await db.execute(text("BEGIN IMMEDIATE"))
                ids = (
                    await db.scalars(
                        select(ComparisonRun.id)
                        .where(eligible)
                        .order_by(ComparisonRun.id)
                        .limit(100)
                    )
                ).all()
                if not ids:
                    return counts
                await delete_contents(db, ids)
                await db.commit()
            except SQLAlchemyError:
                await db.rollback()
                counts["failed"] += len(ids) or 1
                return counts
            counts["deleted"] += len(ids)
            logger.info("Comparison cleanup batch deleted=%d", len(ids))

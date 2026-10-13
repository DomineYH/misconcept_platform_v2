"""Bounded retention batches; usage references and content change atomically."""

import logging

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.models import ComparisonConversation, ComparisonRun
from src.services.comparison_conversation_history import delete_aggregate
from src.services.comparison_history import delete_contents, running_content
from src.services.comparison_state import utcnow

logger = logging.getLogger(__name__)


async def cleanup_comparisons(factory, *, execute=False):
    expired_or_ownerless = or_(
        ComparisonRun.expires_at <= utcnow(), ComparisonRun.admin_id.is_(None)
    )
    expired_or_ownerless = (
        expired_or_ownerless & ComparisonRun.conversation_id.is_(None)
    )
    eligible = expired_or_ownerless & ~running_content()
    expired_parent = or_(
        ComparisonConversation.expires_at <= utcnow(),
        ComparisonConversation.admin_id.is_(None),
    )
    busy_parent = (
        select(ComparisonRun.id)
        .where(
            ComparisonRun.conversation_id == ComparisonConversation.id,
            running_content(),
        )
        .exists()
    )
    eligible_parent = expired_parent & ~busy_parent
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
        counts["targets"] += await db.scalar(
            select(func.count())
            .select_from(ComparisonConversation)
            .where(eligible_parent)
        )
        counts["skipped_running"] += await db.scalar(
            select(func.count())
            .select_from(ComparisonConversation)
            .where(expired_parent, busy_parent)
        )
    if not execute:
        return counts
    while True:
        async with factory() as db:
            ids, parents = [], []
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
                parents = (
                    await db.scalars(
                        select(ComparisonConversation.id)
                        .where(eligible_parent)
                        .order_by(ComparisonConversation.id)
                        .limit(100 - len(ids))
                    )
                ).all()
                if not ids and not parents:
                    return counts
                await delete_contents(db, ids)
                for identifier in parents:
                    await delete_aggregate(db, identifier)
                await db.commit()
            except SQLAlchemyError:
                await db.rollback()
                counts["failed"] += len(ids) + len(parents) or 1
                return counts
            counts["deleted"] += len(ids) + len(parents)
            logger.info(
                "Comparison cleanup batch deleted=%d", len(ids) + len(parents)
            )

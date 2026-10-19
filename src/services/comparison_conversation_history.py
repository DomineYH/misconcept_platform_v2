"""Aggregate history and ledger-preserving deletion under one writer transaction."""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select, text

from src.demo.lifecycle import NORMAL_SCOPE, lifecycle
from src.models import ComparisonConversation, ComparisonRun, GenerationRun
from src.services.comparison_conversations import owned_conversation
from src.services.comparison_history import delete_contents, running_content
from src.services.comparison_state import utcnow
from src.services.generation_runs import conflict


async def list_conversations(db, user_id, cursor=None):
    query = select(ComparisonConversation).where(
        ComparisonConversation.admin_id == user_id,
        ComparisonConversation.expires_at > utcnow(),
    )
    if cursor:
        try:
            created, identifier = cursor.split(",")
            created, identifier = datetime.fromisoformat(created), str(
                UUID(identifier)
            )
            if created.tzinfo is not None:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(
                422, detail={"code": "invalid_cursor"}
            ) from None
        query = query.where(
            or_(
                ComparisonConversation.created_at < created,
                and_(
                    ComparisonConversation.created_at == created,
                    ComparisonConversation.id < identifier,
                ),
            )
        )
    records = (
        await db.scalars(
            query.order_by(
                ComparisonConversation.created_at.desc(),
                ComparisonConversation.id.desc(),
            ).limit(21)
        )
    ).all()
    last = records[19] if len(records) > 20 else None
    return dict(
        items=[
            dict(
                conversation_id=record.id,
                title=record.source_snapshot["title"],
                created_at=record.created_at.isoformat(),
                expires_at=record.expires_at.isoformat(),
                status=record.state_json["dialogue_status"],
                panels=record.panel_configs,
            )
            for record in records[:20]
        ],
        next_cursor=(
            f"{last.created_at.isoformat()},{last.id}" if last else None
        ),
    )


async def aggregate_busy(
    db, identifier, *, scope=NORMAL_SCOPE, registry=lifecycle
):
    ids = select(ComparisonRun.id).where(
        ComparisonRun.conversation_id == identifier
    )
    running = await db.scalar(
        select(ComparisonRun.id).where(
            ComparisonRun.id.in_(ids),
            running_content(scope=scope, registry=registry),
        )
    )
    attempts = await db.scalars(
        select(GenerationRun.id).where(GenerationRun.comparison_run_id.in_(ids))
    )
    return bool(
        running
        or any(
            registry.lookup(scope, "run", run_id) is not None
            for run_id in attempts
        )
    )


async def delete_aggregate(db, identifier):
    ids = (
        await db.scalars(
            select(ComparisonRun.id).where(
                ComparisonRun.conversation_id == identifier
            )
        )
    ).all()
    await delete_contents(db, ids)
    await db.execute(
        delete(ComparisonConversation).where(
            ComparisonConversation.id == identifier
        )
    )


async def delete_conversation(
    db, identifier, owner_id, *, scope=NORMAL_SCOPE, registry=lifecycle
):
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    await owned_conversation(db, identifier, owner_id)
    if await aggregate_busy(db, identifier, scope=scope, registry=registry):
        conflict("comparison_busy")
    await delete_aggregate(db, identifier)
    await db.commit()

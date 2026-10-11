"""Small owner history projections and transactional content deletion."""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from src.models import ApiUsageLog, ComparisonRun, GenerationRun
from src.services.comparison_state import owned_comparison, utcnow
from src.services.generation_lifecycle import active_runs


async def list_comparisons(db, owner_id, cursor=None):
    query = select(ComparisonRun).where(
        ComparisonRun.admin_id == owner_id, ComparisonRun.expires_at > utcnow()
    )
    if cursor is not None:
        try:
            created, identifier = cursor.split(",")
            created = datetime.fromisoformat(created)
            identifier = str(UUID(identifier))
            if created.tzinfo is not None:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(
                422, detail={"code": "invalid_cursor"}
            ) from None
        query = query.where(
            or_(
                ComparisonRun.created_at < created,
                and_(
                    ComparisonRun.created_at == created,
                    ComparisonRun.id < identifier,
                ),
            )
        )
    records = (
        await db.scalars(
            query.order_by(
                ComparisonRun.created_at.desc(), ComparisonRun.id.desc()
            ).limit(21)
        )
    ).all()
    items = []
    for record in records[:20]:
        runs = (
            await db.execute(
                select(GenerationRun.panel_id, GenerationRun.status)
                .where(GenerationRun.comparison_run_id == record.id)
                .order_by(GenerationRun.started_at, GenerationRun.id)
            )
        ).all()
        latest = dict(runs)
        items.append(
            dict(
                comparison_run_id=record.id,
                title=record.source_snapshot.get("title"),
                source_kind=record.source_snapshot.get("source_kind"),
                source_origin=(
                    "saved_scenario_edit"
                    if record.source_snapshot.get("scenario_id") is not None
                    else "new_draft"
                ),
                target_role=record.target_role,
                created_at=record.created_at.isoformat(),
                expires_at=record.expires_at.isoformat(),
                status=record.status,
                panel_statuses=list(latest.values()),
            )
        )
    last = records[19] if len(records) > 20 else None
    return dict(
        items=items,
        next_cursor=(
            f"{last.created_at.isoformat()},{last.id}" if last else None
        ),
    )


def running_content():
    return or_(
        ComparisonRun.status == "running",
        select(GenerationRun.id)
        .where(
            GenerationRun.comparison_run_id == ComparisonRun.id,
            GenerationRun.status == "running",
        )
        .exists(),
        select(ApiUsageLog.id)
        .join(GenerationRun, ApiUsageLog.run_id == GenerationRun.id)
        .where(
            GenerationRun.comparison_run_id == ComparisonRun.id,
            ApiUsageLog.status == "running",
        )
        .exists(),
    )


async def delete_contents(db, comparison_ids):
    runs = select(GenerationRun.id).where(
        GenerationRun.comparison_run_id.in_(comparison_ids)
    )
    await db.execute(
        update(ApiUsageLog)
        .where(ApiUsageLog.run_id.in_(runs))
        .values(run_id=None)
    )
    await db.execute(
        delete(ComparisonRun).where(ComparisonRun.id.in_(comparison_ids))
    )


async def delete_comparison(db, comparison_id, owner_id):
    await db.execute(text("BEGIN IMMEDIATE"))
    await owned_comparison(db, comparison_id, owner_id)
    running = await db.scalar(
        select(ComparisonRun.id).where(
            ComparisonRun.id == comparison_id, running_content()
        )
    )
    runs = await db.scalars(
        select(GenerationRun.id).where(
            GenerationRun.comparison_run_id == comparison_id
        )
    )
    if running or any(run_id in active_runs for run_id in runs):
        raise HTTPException(409, detail={"code": "comparison_busy"})
    try:
        await delete_contents(db, [comparison_id])
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(503, detail={"code": "storage_error"}) from None

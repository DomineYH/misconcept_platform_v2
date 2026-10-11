"""Owner and expiry checks plus saved comparison projections."""

import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from src.models import ApiUsageLog, ComparisonRun, GenerationRun


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def owned_comparison(db, comparison_id, owner_id):
    comparison = await db.get(ComparisonRun, comparison_id)
    if (
        comparison is None
        or comparison.admin_id != owner_id
        or comparison.expires_at <= utcnow()
    ):
        raise HTTPException(404, detail={"code": "comparison_not_found"})
    return comparison


async def comparison_snapshot(db, comparison):
    runs = (
        await db.scalars(
            select(GenerationRun)
            .where(GenerationRun.comparison_run_id == comparison.id)
            .order_by(GenerationRun.started_at, GenerationRun.id)
        )
    ).all()
    panels = []
    for config in comparison.panel_configs:
        run = next(
            item
            for item in reversed(runs)
            if item.panel_id == config["panel_id"]
        )
        usage = await db.scalar(
            select(ApiUsageLog)
            .where(ApiUsageLog.run_id == run.id)
            .order_by(ApiUsageLog.id.desc())
        )

        def elapsed(value):
            return (
                round((value - run.started_at).total_seconds() * 1000)
                if value is not None
                else None
            )

        outcome = json.loads(run.outcome_json) if run.outcome_json else {}
        panels.append(
            dict(
                **config,
                run_id=run.id,
                turn_id=run.turn_id,
                status=run.status,
                text=outcome.get("text", run.partial_text or ""),
                error_code=run.error_code,
                attempts=[],
                timing=dict(
                    started_at=run.started_at.isoformat(),
                    finished_at=(
                        run.finished_at.isoformat() if run.finished_at else None
                    ),
                    first_output_ms=elapsed(run.first_output_at),
                    total_ms=elapsed(run.finished_at),
                ),
                usage=dict(
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    cost=usage.estimated_cost_usd if usage else None,
                ),
                provider_model_id=outcome.get("provider_model_id"),
                provider_request_id=outcome.get("provider_request_id"),
            )
        )
    return dict(
        comparison_run_id=comparison.id,
        request_id=comparison.request_id,
        target_role=comparison.target_role,
        mode=comparison.mode,
        status=comparison.status,
        source_snapshot=comparison.source_snapshot,
        source_hash=comparison.source_hash,
        canonical_context=comparison.canonical_context,
        current_message=comparison.current_message,
        current_message_id=len(comparison.canonical_context) + 1,
        context_hash=comparison.context_hash,
        parent_run_id=comparison.parent_run_id,
        created_at=comparison.created_at.isoformat(),
        expires_at=comparison.expires_at.isoformat(),
        panels=panels,
    )

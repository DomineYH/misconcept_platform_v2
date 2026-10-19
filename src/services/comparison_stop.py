"""Commit cancellation only for the owner's specified current generations."""

from fastapi import HTTPException
from sqlalchemy import text

from src.demo.lifecycle import NORMAL_SCOPE, lifecycle
from src.models import GenerationRun
from src.services.comparison_state import (
    comparison_snapshot,
    find_panel,
    owned_comparison,
    utcnow,
)
from src.services.generation_runs import conflict


async def stop_comparison(
    factory,
    comparison_id,
    owner_id,
    targets,
    *,
    all_panels=False,
    scope=NORMAL_SCOPE,
    registry=lifecycle,
):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        comparison = await owned_comparison(db, comparison_id, owner_id)
        if comparison.conversation_id is not None:
            conflict("use_conversation")
        result, stopped = await stage_stop(
            db, comparison, targets, all_panels=all_panels
        )
        try:
            await db.commit()
        except BaseException as error:
            await db.invalidate()
            if isinstance(error, Exception):
                raise HTTPException(
                    503, detail={"code": "storage_error"}
                ) from None
            raise
        for run_id in stopped:
            registry.cancel(scope, "run", run_id)
        return result


async def stage_stop(db, comparison, targets, *, all_panels=False):
    snapshot = await comparison_snapshot(db, comparison)
    for target in targets:
        panel = find_panel(snapshot, target.panel_id)
        if panel["run_id"] != str(target.generation_id):
            conflict("stale_generation")
    requested = {str(target.generation_id) for target in targets}
    if all_panels and any(
        panel["status"] == "running" and panel["run_id"] not in requested
        for panel in snapshot["panels"]
    ):
        conflict("stale_generation")
    stopped = []
    for target in targets:
        run = await db.get(GenerationRun, str(target.generation_id))
        if run.status == "running":
            run.status, run.error_code = "cancelled", "stopped"
            run.finished_at = utcnow()
            stopped.append(run.id)
    await db.flush()
    result = await comparison_snapshot(db, comparison)
    if all(panel["status"] != "running" for panel in result["panels"]):
        comparison.status = result["status"] = "finished"
    return result, stopped

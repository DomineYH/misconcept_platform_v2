"""Versioned retry and stop reuse the existing child admission and cancellation."""

from copy import deepcopy

from sqlalchemy import text

from src.api.schemas.comparison import ComparisonRetry
from src.demo.lifecycle import NORMAL_SCOPE, lifecycle
from src.models import ComparisonRun
from src.services.call_admission import execution_lock
from src.services.comparison_conversations import (
    action_context,
    conversation_snapshot,
    record_action,
    validate_setup,
)
from src.services.comparison_preparation import prepare_comparison
from src.services.comparison_runs import (
    admit_comparison,
    allocate_panels,
    persist_comparison,
)
from src.services.comparison_stop import stage_stop
from src.services.generation_runs import conflict


async def recover_action(
    factory,
    user,
    identifier,
    action,
    body,
    child_id=None,
    panel_id=None,
    *,
    scope=NORMAL_SCOPE,
    registry=lifecycle,
):
    executions = None
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            key = (
                f"{action}:{child_id}:{panel_id}"
                if action == "retry"
                else action
            )
            parent, input_hash, replay = await action_context(
                db, user, identifier, key, body
            )
            if replay:
                return await conversation_snapshot(db, parent), None
            child_id = child_id if action == "retry" else str(body.child_id)
            child = await db.get(ComparisonRun, child_id)
            if child is None or child.conversation_id != parent.id:
                conflict("stale_generation")
            state = deepcopy(parent.state_json)
            stopped = []
            if action == "retry":
                await validate_setup(db, parent, panel_id)
                allowed = {
                    state["pending_student_child_id"],
                    state["final_analysis_child_id"],
                }
                if state["pairs"]:
                    allowed.update(
                        entry["child_id"]
                        for entry in state["mentor_actions"]
                        if entry["target_turn_id"]
                        == state["pairs"][-1]["turn_id"]
                    )
                if child_id not in allowed:
                    conflict("immutable_round")
                admission = await admit_comparison(
                    db,
                    user,
                    ComparisonRetry(
                        request_id=body.request_id,
                        generation_id=body.generation_id,
                    ),
                    child_id,
                    panel_id,
                    conversation_id=parent.id,
                )
                history, prepared = await prepare_comparison(db, admission.body)
                executions, frozen = await allocate_panels(
                    factory,
                    db,
                    user,
                    admission.body,
                    prepared,
                    admission.frozen,
                )
                snapshot = await persist_comparison(
                    db,
                    user,
                    admission,
                    history,
                    executions,
                    frozen,
                    conversation=parent,
                    commit=False,
                )
            else:
                snapshot, stopped = await stage_stop(
                    db, child, body.generations
                )
            record_action(parent, state, key, body, input_hash, child_id)
            try:
                await db.flush()
                result = await conversation_snapshot(db, parent)
                await db.commit()
            except BaseException:
                if executions:
                    for execution in executions:
                        execution["permit"].release()
                raise
            for run_id in stopped:
                registry.cancel(scope, "run", run_id)
            if executions:
                snapshot.update(
                    conversation_id=parent.id,
                    version=parent.version,
                    receipt=state["receipts"][str(body.request_id)],
                )
                return snapshot, executions
            return result, None

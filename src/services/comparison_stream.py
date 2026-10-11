"""Comparison SSE using the shared invocation and response lifecycle."""

import asyncio
import json
import logging
from contextlib import aclosing, suppress
from datetime import datetime, timezone

import anyio
from starlette.responses import StreamingResponse

from src.services.call_admission import recheck_call
from src.services.call_execution import execute_call
from src.services.comparison_analysis import analysis_comparison_result
from src.services.comparison_runs import finish_comparison
from src.services.comparison_state import COMPLETED_STATUSES, find_panel
from src.services.generation_lifecycle import active_runs
from src.services.invocation_types import InvocationError
from src.services.student_stream import (
    CLEANUP_SECONDS,
    HEARTBEAT_SECONDS,
)

logger = logging.getLogger(__name__)


class ComparisonStreamingResponse(StreamingResponse):
    def __init__(self, factory, snapshot, executions):
        self.factory = factory
        self.children = []
        self.ready = asyncio.Event()
        self.queue = asyncio.Queue(maxsize=len(executions))
        for execution in executions:
            panel = find_panel(snapshot, execution["panel_id"])
            accepted = dict(
                **snapshot,
                run_id=panel["run_id"],
                panel_id=panel["panel_id"],
                turn_id=panel["turn_id"],
            )
            accepted["request_id"] = execution["request"].request_id
            cancelled = active_runs[panel["run_id"]] = asyncio.Event()
            child = dict(accepted=accepted, execution=execution, seq=0)
            child["task"] = asyncio.create_task(self.forward(child, cancelled))
            execution["permit"].task = child["task"]
            self.children.append(child)
        super().__init__(
            self.multiplex(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    async def forward(self, child, cancelled):
        await self.ready.wait()
        async with aclosing(
            stream_comparison(
                self.factory, child["accepted"], child["execution"], cancelled
            )
        ) as events:
            async for frame in events:
                await self.queue.put(frame)
                if frame.startswith("event:"):
                    child["seq"] += 1

    async def multiplex(self):
        self.ready.set()
        pending = {child["task"] for child in self.children}
        reading = None
        try:
            while (
                pending
                or not self.queue.empty()
                or (reading is not None and reading.done())
            ):
                if reading is None:
                    reading = asyncio.create_task(self.queue.get())
                done, _ = await asyncio.wait(
                    pending | {reading},
                    timeout=HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if reading in done:
                    yield reading.result()
                    reading = None
                elif not done:
                    yield ": ping\n\n"
                for child in self.children:
                    task = child["task"]
                    if task not in done:
                        continue
                    pending.discard(task)
                    child["execution"]["permit"].release()
                    active_runs.pop(child["accepted"]["run_id"], None)
                    error = None if task.cancelled() else task.exception()
                    if child.get("recovering") and error is not None:
                        logger.error(
                            "Comparison recovery task failed", exc_info=error
                        )
                    if not child.get("recovering") and (
                        task.cancelled() or error is not None
                    ):
                        child["recovering"] = True
                        child["task"] = asyncio.create_task(self.recover(child))
                        pending.add(child["task"])
        finally:
            if reading is not None:
                reading.cancel()
                with suppress(asyncio.CancelledError):
                    await reading

    async def recover(self, child):
        accepted = child["accepted"]
        try:
            result = await finish_comparison(
                self.factory,
                accepted["run_id"],
                status="interrupted",
                error_code="configuration_unavailable",
            )
            terminal = terminal_frame(accepted, result, max(1, child["seq"]))
        except Exception:
            logger.exception("Comparison child recovery failed")
            terminal = sse_frame(
                accepted,
                "run.failed",
                max(1, child["seq"]),
                error_code="storage_error",
            )
        if child["seq"] == 0:
            await self.queue.put(
                sse_frame(
                    accepted, "run.accepted", 0, **accepted_fields(accepted)
                )
            )
        # Keep recovery behind already queued frames from this run.
        await self.queue.put(terminal)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(CLEANUP_SECONDS):
                    for child in self.children:
                        child["task"].cancel()
                    await asyncio.gather(
                        *(child["task"] for child in self.children),
                        return_exceptions=True,
                    )
                    await self.body_iterator.aclose()
                    for child in self.children:
                        run_id = child["accepted"]["run_id"]
                        if active_runs.pop(run_id, None) is not None:
                            try:
                                await finish_comparison(
                                    self.factory,
                                    run_id,
                                    status="interrupted",
                                    error_code="disconnected",
                                )
                            except Exception:
                                logger.error(
                                    "Unable to record unsent comparison"
                                )
                for child in self.children:
                    active_runs.pop(child["accepted"]["run_id"], None)
                    child["execution"]["permit"].release()


def sse_frame(accepted, kind, seq, **data):
    payload = dict(
        comparison_run_id=accepted["comparison_run_id"],
        panel_id=accepted["panel_id"],
        run_id=accepted["run_id"],
        turn_id=accepted["turn_id"],
        seq=seq,
        **data,
    )
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def accepted_fields(accepted):
    panel = find_panel(accepted, accepted["panel_id"])
    return dict(
        request_id=accepted["request_id"],
        context_hash=accepted["context_hash"],
        source_snapshot=accepted["source_snapshot"],
        canonical_context=accepted["canonical_context"],
        current_message_id=accepted["current_message_id"],
        created_at=accepted["created_at"],
        expires_at=accepted["expires_at"],
        model_config=panel["model_config"],
        applied_options=panel["applied_options"],
    )


def terminal_frame(accepted, snapshot, seq):
    panel = find_panel(snapshot, accepted["panel_id"])
    state = next(
        attempt
        for attempt in [panel, *panel["attempts"]]
        if attempt["run_id"] == accepted["run_id"]
    )
    state = {**state, "outcome_status": state["status"]}
    return sse_frame(
        accepted,
        (
            "output.completed"
            if state["status"] in COMPLETED_STATUSES
            else f"run.{state['status']}"
        ),
        seq,
        **{
            key: value
            for key, value in state.items()
            if key not in ("run_id", "panel_id", "turn_id")
        },
    )


async def stream_comparison(factory, accepted, execution, cancelled):
    seq, partial, first_output = 0, "", None
    task = reading = None
    queue = asyncio.Queue(maxsize=1)
    permit = execution["permit"]
    expires = datetime.fromisoformat(accepted["expires_at"]).replace(
        tzinfo=timezone.utc
    )
    remaining = max(0, (expires - datetime.now(timezone.utc)).total_seconds())
    elapsed = asyncio.get_running_loop().time() - permit.admitted_at
    total_key = f"{permit.role}_total"
    permit.timeouts = {
        **permit.timeouts,
        total_key: min(permit.timeouts[total_key], elapsed + remaining),
    }
    terminal, closing = False, False
    cancellation = asyncio.create_task(cancelled.wait())

    def frame(kind, **data):
        nonlocal seq
        value = sse_frame(accepted, kind, seq, **data)
        seq += 1
        return value

    async def finish(status, **data):
        return await finish_comparison(
            factory,
            accepted["run_id"],
            status=status,
            first_output_at=first_output,
            **data,
        )

    async def produce():
        permit.task = asyncio.current_task()
        async with aclosing(
            execute_call(
                permit,
                execution["request"],
                kind=(
                    "stream"
                    if execution["request"].role == "student"
                    else "structured"
                ),
                run_id=accepted["run_id"],
                usage_scope="comparison",
            )
        ) as events:
            async for event in events:
                if closing:
                    return
                await queue.put(event)

    try:
        yield frame("run.accepted", **accepted_fields(accepted))
        task = asyncio.create_task(produce())
        while True:
            reading = asyncio.create_task(queue.get())
            while True:
                done, _ = await asyncio.wait(
                    {reading, task, cancellation},
                    timeout=HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation in done:
                    closing = True
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                    result = await finish(
                        "cancelled",
                        partial_text=partial or None,
                        error_code="stopped",
                    )
                    terminal = True
                    yield terminal_frame(accepted, result, seq)
                    return
                if reading in done:
                    break
                if task in done:
                    if not queue.empty():
                        await reading
                        break
                    raise RuntimeError("upstream_ended")
                yield ": ping\n\n"
            event = reading.result()
            analysis_result = (
                analysis_comparison_result(
                    accepted, execution["request"], event
                )
                if execution["request"].role == "analysis"
                else None
            )
            if analysis_result is not None:
                await recheck_call(permit)
                status, validated, error_code = analysis_result
                result = await finish(
                    status,
                    result=validated,
                    error_code=error_code,
                    provider_model_id=event.provider_model_id,
                    provider_request_id=event.provider_request_id,
                )
                terminal = True
                yield terminal_frame(accepted, result, seq)
                return
            if (
                execution["request"].role == "student"
                and event.type == "text_delta"
                and event.text
            ):
                partial += event.text
                if first_output is None and event.text.strip():
                    first_output = datetime.now(timezone.utc).replace(
                        tzinfo=None
                    )
                yield frame("output.delta", text=event.text)
            elif event.type == "completed":
                await recheck_call(permit)
                result = await finish(
                    "completed",
                    content=event.text or partial,
                    mentor_result=(
                        event.structured
                        if execution["request"].role == "mentor"
                        else None
                    ),
                    provider_model_id=event.provider_model_id,
                    provider_request_id=event.provider_request_id,
                )
                terminal = True
                yield terminal_frame(accepted, result, seq)
                return
            elif event.type not in ("usage", "text_delta"):
                result = await finish(
                    "interrupted" if event.type == "interrupted" else "failed",
                    partial_text=partial or None,
                    error_code=event.error_code or "provider_error",
                )
                terminal = True
                yield terminal_frame(accepted, result, seq)
                return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        try:
            result = await finish(
                "failed",
                partial_text=partial or None,
                error_code=(
                    error.code
                    if isinstance(error, InvocationError)
                    else "storage_error"
                ),
            )
            terminal = True
            yield terminal_frame(accepted, result, seq)
        except Exception:
            logger.error("Comparison finalization failed")
            yield frame("run.failed", error_code="storage_error")
    finally:
        active_runs.pop(accepted["run_id"], None)
        closing = True
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                cancellation.cancel()
                if reading is not None:
                    reading.cancel()
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                with suppress(asyncio.CancelledError):
                    await cancellation
                if reading is not None:
                    with suppress(asyncio.CancelledError):
                        await reading
                if not terminal:
                    try:
                        await finish(
                            "interrupted",
                            partial_text=partial or None,
                            error_code="disconnected",
                        )
                    except Exception:
                        logger.error(
                            "Comparison interruption could not be saved"
                        )
            permit.release()

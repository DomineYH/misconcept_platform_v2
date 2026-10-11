"""Student comparison SSE using the shared invocation and response lifecycle."""

import asyncio
import json
import logging
from contextlib import aclosing, suppress
from datetime import datetime, timezone

import anyio

from src.services.call_admission import recheck_call
from src.services.call_execution import execute_call
from src.services.comparison_runs import finish_comparison
from src.services.generation_lifecycle import active_runs
from src.services.invocation_types import InvocationError
from src.services.student_stream import (
    CLEANUP_SECONDS,
    HEARTBEAT_SECONDS,
    StudentStreamingResponse,
)

logger = logging.getLogger(__name__)


def comparison_response(factory, snapshot, execution):
    panel = snapshot["panels"][0]
    accepted = dict(
        **snapshot,
        run_id=panel["run_id"],
        panel_id=panel["panel_id"],
        turn_id=panel["turn_id"],
    )
    return StudentStreamingResponse(
        factory,
        accepted,
        execution,
        stream_generator=stream_comparison,
        finisher=finish_comparison,
    )


async def stream_comparison(factory, accepted, execution, started, cancelled):
    seq, partial, first_output = 0, "", None
    task = reading = None
    queue = asyncio.Queue(maxsize=1)
    permit = execution["permit"]
    terminal, closing = False, False
    cancellation = asyncio.create_task(cancelled.wait())

    def frame(kind, **data):
        nonlocal seq
        payload = dict(
            comparison_run_id=accepted["comparison_run_id"],
            panel_id=accepted["panel_id"],
            run_id=accepted["run_id"],
            turn_id=accepted["turn_id"],
            seq=seq,
            **data,
        )
        seq += 1
        return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def finish(status, **data):
        return await finish_comparison(
            factory,
            accepted["run_id"],
            status=status,
            first_output_at=first_output,
            **data,
        )

    def terminal_frame(snapshot):
        state = snapshot["panels"][0]
        return frame(
            (
                "output.completed"
                if state["status"] == "completed"
                else f"run.{state['status']}"
            ),
            **{
                key: value
                for key, value in state.items()
                if key not in ("run_id", "panel_id", "turn_id")
            },
        )

    async def produce():
        permit.task = asyncio.current_task()
        async with aclosing(
            execute_call(
                permit,
                execution["request"],
                kind="stream",
                run_id=accepted["run_id"],
                usage_scope="comparison",
            )
        ) as events:
            async for event in events:
                if closing:
                    return
                await queue.put(event)

    try:
        yield frame(
            "run.accepted",
            request_id=accepted["request_id"],
            context_hash=accepted["context_hash"],
            source_snapshot=accepted["source_snapshot"],
            canonical_context=accepted["canonical_context"],
            current_message_id=accepted["current_message_id"],
            created_at=accepted["created_at"],
            expires_at=accepted["expires_at"],
            model_config=accepted["panels"][0]["model_config"],
        )
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
                    raise asyncio.CancelledError
                if reading in done:
                    break
                if task in done:
                    if not queue.empty():
                        await reading
                        break
                    raise RuntimeError("upstream_ended")
                yield ": ping\n\n"
            event = reading.result()
            if event.type == "text_delta" and event.text:
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
                    provider_model_id=event.provider_model_id,
                    provider_request_id=event.provider_request_id,
                )
                terminal = True
                yield terminal_frame(result)
                return
            elif event.type not in ("usage", "text_delta"):
                result = await finish(
                    "interrupted" if event.type == "interrupted" else "failed",
                    partial_text=partial or None,
                    error_code=event.error_code or "provider_error",
                )
                terminal = True
                yield terminal_frame(result)
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
            yield terminal_frame(result)
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

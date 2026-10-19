"""Final-only mentor SSE after the structured result is committed."""

import asyncio
import json
import logging
import time
from contextlib import nullcontext, suppress

import anyio

from src.demo.database import DemoError
from src.i18n import translate
from src.models import GenerationRun
from src.services.generation_lifecycle import active_runs
from src.services.generation_runs import snapshot
from src.services.invocation_types import InvocationError
from src.services.mentor_generation import finish_mentor
from src.services.student_stream import (
    CLEANUP_SECONDS,
    HEARTBEAT_SECONDS,
    StudentStreamError,
    StudentStreamingResponse,
    demo_kwargs,
    revoked_frame,
)
from src.services.tutor_bot import TutorBot

logger = logging.getLogger(__name__)


def mentor_response(factory, accepted, execution, *, demo_run=None):
    return StudentStreamingResponse(
        factory,
        accepted,
        execution,
        stream_generator=stream_mentor,
        finisher=finish_mentor,
        demo_run=demo_run,
    )


async def stream_mentor(factory, accepted, execution, started, cancelled):
    language = execution.get("display_language", "ko")
    seq = 0
    last_sent = started
    bot = task = None
    terminal = False
    cancellation = asyncio.create_task(cancelled.wait())
    demo_run = execution.get("demo_run")
    claim = execution.get("claim") or (
        lambda: active_runs.pop(accepted["run_id"], None)
    )

    def frame(kind, **data):
        nonlocal seq, last_sent
        last_sent = time.monotonic()
        payload = {
            "run_id": accepted["run_id"],
            "turn_id": accepted["turn_id"],
            "seq": seq,
            **data,
        }
        if demo_run is not None:
            payload.update(demo_run.fields(kind, data))
        seq += 1
        encoded = json.dumps(payload, ensure_ascii=False)
        return f"event: {kind}\ndata: {encoded}\n\n"

    async def current():
        # §11: a revoked context gets only a terminal without body text.
        if demo_run is not None and (error := await demo_run.revoked()):
            raise error

    async def feedback():
        # The bot can outlive bounded cleanup; it holds its own lease.
        async with demo_run.child() if demo_run else nullcontext():
            return await bot.generate_feedback()

    def terminal_frame(result):
        if result["status"] == "completed":
            return frame(
                "output.completed",
                status="completed",
                result_kind=result["result_kind"],
                message=result["message"],
                turn_index=result["turn_index"],
            )
        return frame(
            f"run.{result['status']}",
            status=result["status"],
            code=result["error_code"],
            retryable=result["retryable"],
            message=(
                translate("teacher.conversation_ended", language)
                if result["status"] == "cancelled"
                else translate("teacher.mentor_failed", language)
            ),
        )

    try:
        yield frame(
            "run.accepted",
            request_id=accepted["request_id"],
            turn_index=accepted["turn_index"],
            teacher_message_id=accepted["teacher_message_id"],
            operation="mentor",
            status="running",
        )
        async with factory() as db:
            run = await db.get(GenerationRun, accepted["run_id"])
            state = await snapshot(db, run) if run.status != "running" else None
        if state is not None:
            terminal = True
            yield terminal_frame(state)
            return
        if cancelled.is_set():
            raise StudentStreamError("session_ended")
        bot = TutorBot(
            request=execution["request"],
            session_id=accepted["session_id"],
            run_id=accepted["run_id"],
            permit=execution["permit"],
            demo_adapter=execution.get("demo_adapter"),
        )
        task = asyncio.create_task(feedback())
        while True:
            remaining = (
                execution["deadline"] - time.monotonic()
                if execution["deadline"] is not None
                else float("inf")
            )
            if remaining <= 0:
                raise StudentStreamError("timeout_total")
            idle = HEARTBEAT_SECONDS - (time.monotonic() - last_sent)
            if idle <= 0:
                last_sent = time.monotonic()
                yield ": ping\n\n"
                continue
            done, _ = await asyncio.wait(
                {task, cancellation},
                timeout=min(idle, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done:
                raise StudentStreamError("session_ended")
            if task not in done:
                continue
            content, reason = task.result()
            try:
                result = await finish_mentor(
                    factory,
                    accepted["run_id"],
                    status="completed",
                    content=content,
                    reason_summary=reason,
                    **demo_kwargs(demo_run),
                )
            except DemoError:
                raise
            except Exception:
                logger.error("Mentor final commit failed")
                raise StudentStreamError("storage_error") from None
            await current()
            terminal = True
            yield terminal_frame(result)
            return
    except asyncio.CancelledError:
        raise
    except DemoError as error:
        # The run's DB is retired: nothing is saved there and no body is sent.
        terminal = True
        yield revoked_frame(
            frame, error, translate("teacher.mentor_failed", language)
        )
    except Exception as error:
        code = (
            error.code
            if isinstance(error, (StudentStreamError, InvocationError))
            else "configuration_unavailable"
        )
        try:
            result = await finish_mentor(
                factory,
                accepted["run_id"],
                status=(
                    "cancelled"
                    if code == "session_ended"
                    else "interrupted" if code == "interrupted" else "failed"
                ),
                error_code=code,
                **demo_kwargs(demo_run),
            )
            await current()
            terminal = True
            yield terminal_frame(result)
        except DemoError as error:
            terminal = True
            yield revoked_frame(
                frame, error, translate("teacher.mentor_failed", language)
            )
        except Exception:
            logger.error("Unable to persist mentor failure")
            yield frame(
                "run.interrupted",
                status="interrupted",
                code="storage_error",
                message=translate("teacher.mentor_check_execution", language),
                retryable=False,
            )
    finally:
        claim()

        async def close_upstream():
            cancellation.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task

        async def close_client():
            if bot is not None:
                with suppress(Exception):
                    await bot.close()

        async def interrupt():
            try:
                await finish_mentor(
                    factory,
                    accepted["run_id"],
                    status="interrupted",
                    error_code="disconnected",
                    **demo_kwargs(demo_run),
                )
            except Exception:
                logger.error("Unable to record interrupted mentor")

        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                if task is not None:
                    task.cancel()
                async with anyio.create_task_group() as cleanup:
                    cleanup.start_soon(close_upstream)
                    cleanup.start_soon(close_client)
                    if not terminal:
                        cleanup.start_soon(interrupt)

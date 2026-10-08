"""Final-only mentor SSE using the existing non-streaming judgment policy."""

import asyncio
import json
import logging
import time
from contextlib import suppress

import anyio

from src.models import GenerationRun
from src.services.generation_lifecycle import active_runs
from src.services.generation_runs import snapshot
from src.services.mentor_generation import finish_mentor
from src.services.student_stream import (
    CLEANUP_SECONDS,
    HEARTBEAT_SECONDS,
    RUN_SECONDS,
    StudentStreamError,
    StudentStreamingResponse,
)
from src.services.tutor_bot import TutorBot

logger = logging.getLogger(__name__)


def mentor_response(factory, accepted, execution):
    return StudentStreamingResponse(
        factory,
        accepted,
        execution,
        stream_generator=stream_mentor,
        finisher=finish_mentor,
    )


async def stream_mentor(factory, accepted, execution, started, cancelled):
    seq = 0
    last_sent = started
    bot = task = None
    terminal = False
    cancellation = asyncio.create_task(cancelled.wait())

    def frame(kind, **data):
        nonlocal seq, last_sent
        last_sent = time.monotonic()
        payload = {
            "run_id": accepted["run_id"],
            "turn_id": accepted["turn_id"],
            "seq": seq,
            **data,
        }
        seq += 1
        encoded = json.dumps(payload, ensure_ascii=False)
        return f"event: {kind}\ndata: {encoded}\n\n"

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
                "대화가 종료되었습니다."
                if result["status"] == "cancelled"
                else "멘토 코칭 생성에 실패했습니다."
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
        bot = TutorBot(None, **execution["options"])
        history = execution["history"]
        task = asyncio.create_task(
            bot.generate_feedback(
                history[-2]["content"],
                history[-1]["content"],
                history[:-2],
                template_text=execution["template"],
                question_counted=True,
            )
        )
        while True:
            remaining = started + RUN_SECONDS - time.monotonic()
            if remaining <= 0:
                raise StudentStreamError("run_timeout")
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
            content, usage = task.result()
            try:
                result = await finish_mentor(
                    factory,
                    accepted["run_id"],
                    status="completed",
                    content=content,
                    usage=usage,
                )
            except Exception:
                logger.exception("Mentor final commit failed")
                raise StudentStreamError("storage_error") from None
            terminal = True
            yield terminal_frame(result)
            return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        code = (
            error.code
            if isinstance(error, StudentStreamError)
            else "provider_error"
        )
        try:
            result = await finish_mentor(
                factory,
                accepted["run_id"],
                status="cancelled" if code == "session_ended" else "failed",
                error_code=code,
            )
            terminal = True
            yield terminal_frame(result)
        except Exception:
            logger.exception("Unable to persist mentor failure")
            yield frame(
                "run.interrupted",
                status="interrupted",
                code="storage_error",
                message="멘토 실행 상태를 확인해주세요.",
                retryable=False,
            )
    finally:
        active_runs.pop(accepted["run_id"], None)

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
                )
            except Exception:
                logger.exception("Unable to record interrupted mentor")

        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                if task is not None:
                    task.cancel()
                async with anyio.create_task_group() as cleanup:
                    cleanup.start_soon(close_upstream)
                    cleanup.start_soon(close_client)
                    if not terminal:
                        cleanup.start_soon(interrupt)

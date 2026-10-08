"""Student-only SSE, with bounded upstream reads and durable finalization."""

import asyncio
import json
import logging
import time
from contextlib import aclosing, suppress
from datetime import datetime, timezone

import anyio
from starlette.responses import StreamingResponse

from src.models import GenerationRun
from src.services.call_execution import execute_call
from src.services.generation_lifecycle import active_runs
from src.services.generation_runs import finish_student, snapshot

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15
# S0 mentor imports these until its A13 transition.
FIRST_OUTPUT_SECONDS = 60
RUN_SECONDS = 180
CLEANUP_SECONDS = 5


class StudentStreamError(Exception):
    def __init__(self, code):
        self.code = code


class StudentStreamingResponse(StreamingResponse):
    def __init__(
        self,
        factory,
        accepted,
        kwargs,
        *,
        stream_generator=None,
        finisher=finish_student,
    ):
        self.factory, self.run_id = factory, accepted["run_id"]
        self.permit = kwargs.get("permit")
        self.finisher = finisher
        cancelled = active_runs[self.run_id] = asyncio.Event()
        super().__init__(
            (stream_generator or stream_student)(
                factory, accepted, kwargs, time.monotonic(), cancelled
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Disconnect during ASGI send can leave the generator suspended.
            if self.permit is not None:
                self.permit.release()
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(CLEANUP_SECONDS):
                    await self.body_iterator.aclose()
                    if active_runs.pop(self.run_id, None) is not None:
                        # Response headers can fail before the generator starts.
                        try:
                            await self.finisher(
                                self.factory,
                                self.run_id,
                                status="interrupted",
                                error_code="disconnected",
                            )
                        except Exception:
                            logger.error("Unable to record unsent generation")


async def stream_student(factory, accepted, kwargs, started, cancelled):
    seq = 0
    partial = ""
    first_output_at = None
    task = reading = None
    queue = asyncio.Queue(maxsize=1)
    permit = kwargs["permit"]
    terminal = False
    closing = False
    last_sent = started
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

    async def finish(status, **data):
        return await finish_student(
            factory,
            accepted["run_id"],
            status=status,
            first_output_at=first_output_at,
            **data,
        )

    def terminal_frame(result):
        if result["status"] == "completed":
            return frame(
                "output.completed",
                status="completed",
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
                else (
                    "관리자에게 AI 연결과 학생 모델 검증을 요청해주세요."
                    if result["error_code"] == "configuration_unavailable"
                    else "학생 응답 생성에 실패했습니다."
                )
            ),
        )

    try:
        yield frame(
            "run.accepted",
            request_id=accepted["request_id"],
            turn_index=accepted["turn_index"],
            teacher_message_id=accepted["teacher_message_id"],
            operation="student",
            status="running",
        )
        # End can commit between reservation and live-signal registration.
        async with factory() as db:
            run = await db.get(GenerationRun, accepted["run_id"])
            state = await snapshot(db, run) if run.status != "running" else None
        if state is not None:
            terminal = True
            yield terminal_frame(state)
            return
        if cancelled.is_set():
            raise StudentStreamError("session_ended")

        async def produce():
            # Reservation hands execution ownership to this response's worker.
            permit.task = asyncio.current_task()
            async with aclosing(
                execute_call(
                    permit,
                    kwargs["request"],
                    kind="stream",
                    run_id=accepted["run_id"],
                    session_id=accepted["session_id"],
                )
            ) as events:
                async for event in events:
                    if closing:
                        return
                    await queue.put(event)

        task = asyncio.create_task(produce())
        while True:
            reading = asyncio.create_task(queue.get())
            while True:
                idle_remaining = HEARTBEAT_SECONDS - (
                    time.monotonic() - last_sent
                )
                done, _ = await asyncio.wait(
                    {reading, cancellation, task},
                    timeout=max(0, idle_remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation in done:
                    raise StudentStreamError("session_ended")
                if reading in done:
                    break
                if task in done:
                    if not queue.empty():
                        await reading
                        break
                    raise StudentStreamError("configuration_unavailable")
                yield ": ping\n\n"
                last_sent = time.monotonic()
            event = reading.result()
            if event.type == "text_delta":
                if not event.text:
                    continue
                partial += event.text
                if first_output_at is None and event.text.strip():
                    first_output_at = datetime.now(timezone.utc)
                yield frame("output.delta", text=event.text)
            elif event.type == "completed":
                try:
                    result = await finish("completed", content=event.text)
                except Exception:
                    logger.error("Student final commit failed")
                    raise StudentStreamError("storage_error") from None
                terminal = True
                yield terminal_frame(result)
                return
            elif event.type != "usage":
                # Keep the existing browser codes; the attempt ledger retains
                # the provider-neutral, more precise error classification.
                code = {
                    "empty_response": "empty_output",
                    "timeout_first_output": "first_output_timeout",
                    "timeout_total": "run_timeout",
                    "output_limit": "incomplete_response",
                    "interrupted": "configuration_unavailable",
                }.get(event.error_code, event.error_code)
                raise StudentStreamError(code or "provider_error")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        code = (
            error.code
            if isinstance(error, StudentStreamError)
            else "provider_error"
        )
        try:
            result = await finish(
                "cancelled" if code == "session_ended" else "failed",
                partial_text=partial or None,
                error_code=code,
            )
            terminal = True
            yield terminal_frame(result)
        except Exception:
            logger.error("Unable to record student failure")
            yield frame(
                "run.failed",
                status="failed",
                code="storage_error",
                message="학생 응답 저장에 실패했습니다.",
                retryable=True,
            )
    finally:
        active_runs.pop(accepted["run_id"], None)
        cancellation.cancel()

        async def close_upstream():
            if reading is not None:
                reading.cancel()
                with suppress(asyncio.CancelledError):
                    await reading
            with suppress(asyncio.CancelledError):
                await cancellation
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task

        async def interrupt():
            try:
                await finish(
                    "interrupted",
                    partial_text=partial or None,
                    error_code="disconnected",
                )
            except Exception:
                logger.error("Unable to record interrupted student")

        # Starlette disconnect cancels its AnyIO scope; shield bounded cleanup.
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                closing = True
                if task is not None:
                    task.cancel()
                # A stalled DB must not prevent SDK close (or vice versa).
                async with anyio.create_task_group() as cleanup:
                    cleanup.start_soon(close_upstream)
                    if not terminal:
                        cleanup.start_soon(interrupt)
            permit.release()

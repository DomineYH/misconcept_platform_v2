"""Student-only SSE, with bounded upstream reads and durable finalization."""

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone

import anyio
from starlette.responses import StreamingResponse

from src.models import GenerationRun
from src.services.base import OpenAIBaseService
from src.services.generation_lifecycle import active_runs
from src.services.generation_runs import finish_student, snapshot
from src.utils.openai_helpers import extract_response_text, extract_usage_dict

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15
FIRST_OUTPUT_SECONDS = 60
RUN_SECONDS = 180
CLEANUP_SECONDS = 5


class StudentStreamError(Exception):
    def __init__(self, code):
        self.code = code


class StudentStreamingResponse(StreamingResponse):
    def __init__(self, factory, accepted, kwargs):
        self.factory, self.run_id = factory, accepted["run_id"]
        cancelled = active_runs[self.run_id] = asyncio.Event()
        super().__init__(
            stream_student(
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
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(CLEANUP_SECONDS):
                    await self.body_iterator.aclose()
                    if active_runs.pop(self.run_id, None) is not None:
                        # Response headers can fail before the generator starts.
                        try:
                            await finish_student(
                                self.factory,
                                self.run_id,
                                status="interrupted",
                                error_code="disconnected",
                            )
                        except Exception:
                            logger.exception("Unable to record unsent student")


async def stream_student(factory, accepted, kwargs, started, cancelled):
    seq = 0
    partial = ""
    first_output_at = None
    usage = None
    service = stream = task = opening = None
    terminal = False
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
            usage=usage,
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
                else "학생 응답 생성에 실패했습니다."
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
        service = OpenAIBaseService()
        # Bypass the non-streaming application's retry decorator deliberately.
        task = opening = asyncio.create_task(
            service.client.responses.create(**kwargs, stream=True)
        )
        iterator = None
        while True:
            deadline = started + RUN_SECONDS
            code = "run_timeout"
            if first_output_at is None:
                deadline = min(deadline, started + FIRST_OUTPUT_SECONDS)
                code = "first_output_timeout"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StudentStreamError(code)
            idle_remaining = HEARTBEAT_SECONDS - (time.monotonic() - last_sent)
            if idle_remaining <= 0:
                last_sent = time.monotonic()
                yield ": ping\n\n"
                continue
            done, _ = await asyncio.wait(
                {task, cancellation},
                timeout=min(idle_remaining, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done:
                raise StudentStreamError("session_ended")
            if not done:
                if time.monotonic() >= deadline:
                    raise StudentStreamError(code)
                last_sent = time.monotonic()
                yield ": ping\n\n"
                continue
            if iterator is None:
                stream = task.result()
                iterator = stream.__aiter__()
            else:
                try:
                    event = task.result()
                except StopAsyncIteration:
                    raise StudentStreamError("incomplete_response") from None
                if event.type == "response.output_text.delta":
                    if event.delta:
                        partial += event.delta
                        if first_output_at is None and event.delta.strip():
                            first_output_at = datetime.now(timezone.utc)
                        yield frame("output.delta", text=event.delta)
                elif event.type.startswith("response.refusal."):
                    raise StudentStreamError("refused")
                elif event.type in {"response.failed", "error"}:
                    usage = extract_usage_dict(getattr(event, "response", None))
                    raise StudentStreamError("provider_error")
                elif event.type == "response.incomplete":
                    usage = extract_usage_dict(event.response)
                    raise StudentStreamError("incomplete_response")
                elif event.type == "response.completed":
                    response = event.response
                    usage = extract_usage_dict(response)
                    if response.status != "completed":
                        raise StudentStreamError("incomplete_response")
                    if any(
                        block.type == "refusal"
                        for item in response.output
                        if item.type == "message"
                        for block in item.content
                    ):
                        raise StudentStreamError("refused")
                    try:
                        content = extract_response_text(response)
                    except ValueError:
                        raise StudentStreamError("empty_output") from None
                    try:
                        result = await finish("completed", content=content)
                    except Exception:
                        logger.exception("Student final commit failed")
                        raise StudentStreamError("storage_error") from None
                    terminal = True
                    yield terminal_frame(result)
                    return
            task = asyncio.create_task(anext(iterator))
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
            logger.exception("Unable to record student failure")
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
            nonlocal stream
            with suppress(asyncio.CancelledError):
                await cancellation
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            if stream is None and opening is not None and opening.done():
                with suppress(asyncio.CancelledError, Exception):
                    stream = opening.result()
            if stream is not None:
                with suppress(Exception):
                    await stream.close()

        async def close_client():
            if service is not None:
                with suppress(Exception):
                    await service.close()

        async def interrupt():
            try:
                await finish(
                    "interrupted",
                    partial_text=partial or None,
                    error_code="disconnected",
                )
            except Exception:
                logger.exception("Unable to record interrupted student")

        # Starlette disconnect cancels its AnyIO scope; shield bounded cleanup.
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                if task is not None:
                    task.cancel()
                # A stalled DB must not prevent SDK close (or vice versa).
                async with anyio.create_task_group() as cleanup:
                    cleanup.start_soon(close_upstream)
                    cleanup.start_soon(close_client)
                    if not terminal:
                        cleanup.start_soon(interrupt)

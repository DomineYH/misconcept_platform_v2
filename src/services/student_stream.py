"""Student-only SSE, with bounded upstream reads and durable finalization."""

import asyncio
import json
import logging
import time
from contextlib import aclosing, nullcontext, suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import anyio
from starlette.responses import StreamingResponse

from src.demo.database import DemoError
from src.i18n import translate
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


# §11 recovery keys for the existing lowercase codes and their SSE aliases.
RECOVERY = {
    **dict.fromkeys(
        ("interrupted", "disconnected", "session_ended"), "demo.stopped"
    ),
    **dict.fromkeys(
        (
            "invalid_output",
            "invalid_json",
            "invalid_reference",
            "empty_response",
            "empty_output",
        ),
        "demo.output_failed",
    ),
    "refused": "demo.output_refused",
    **dict.fromkeys(
        (
            "timeout_connect",
            "timeout_first_output",
            "timeout_total",
            "first_output_timeout",
            "run_timeout",
            "call_limit_reached",
            "rate_limited",
            "transient",
            "storage_error",
            "provider_error",
        ),
        "demo.retry_later",
    ),
    **dict.fromkeys(
        ("context_limit", "output_limit", "incomplete_response"),
        "demo.supported_only",
    ),
}
# §11 boundary codes whose SSE terminal is run.cancelled.
CANCELLED = {
    "DEMO_SESSION_REQUIRED",
    "DEMO_EXPIRED",
    "DEMO_STALE_GENERATION",
    "DEMO_STALE_ROLE",
    "DEMO_RESET_IN_PROGRESS",
    "DEMO_DRAIN_TIMEOUT",
    "DEMO_ROLE_DELETED",
}


class StudentStreamError(Exception):
    def __init__(self, code, canonical=None):
        self.code = code
        # The code before its legacy SSE alias; demo recovery follows it.
        self.canonical = canonical


@dataclass(eq=False)
class DemoRun:
    """A registered demo run; its token owns the registry lease until pop."""

    binding: object
    token: object
    cancelled: asyncio.Event
    claimed: bool = False
    tasks: list = field(default_factory=list)

    def claim(self):
        claimed, self.claimed = self.claimed, True
        return not claimed

    def fields(self, kind, data, canonical=None):
        ctx = self.binding.ctx
        fields = dict(
            generation=ctx.generation,
            auth_epoch=ctx.auth_epoch,
            browser_scope_id=ctx.browser_scope_id,
        )
        if kind.startswith("run.") and kind != "run.accepted":
            code = canonical if canonical in RECOVERY else data.get("code")
            fields["recovery"] = (
                DemoError(code).recovery
                if code and code.startswith("DEMO_")
                else RECOVERY.get(code, "demo.unavailable")
            )
        return fields

    async def revoked(self):
        """The boundary error when the fixed context is no longer current."""
        try:
            await self.binding.registry.assert_current(self.binding.ctx)
        except DemoError as error:
            return error
        return None

    def child(self):
        return self.binding.registry.child_context(self.binding.ctx)

    def track(self, task):
        self.tasks.append(task)

    async def release(self):
        # §6: bounded cleanup can end while child work still runs; done/pop
        # must follow the work, not the cleanup deadline.
        if self.tasks:
            await asyncio.wait(self.tasks)
            for task in self.tasks:
                if not task.cancelled():
                    task.exception()
        self.token.task_control.done.set()
        await self.binding.registry.pop(*self.token.key, self.token)


def demo_kwargs(demo_run):
    """Final saves of a demo run hold its write guard."""
    return {} if demo_run is None else {"demo": demo_run.binding}


def bind_child(permit, request, child):
    """A producer runs on its own child lease, never the registry's."""
    if child is None:
        return request
    permit.ctx = child
    return request if request is None else replace(request, demo_context=child)


def provenance_of(result):
    """§19: a demo result's provenance rides on its terminal frame."""
    return (
        {"provenance": result["provenance"]} if "provenance" in result else {}
    )


def revoked_frame(frame, error, message):
    """§11: a retired DB or changed role ends with a body-free terminal."""
    status = "cancelled" if error.code in CANCELLED else "failed"
    return frame(
        f"run.{status}",
        status=status,
        code=error.code,
        retryable=error.retryable,
        message=message,
    )


class StudentStreamingResponse(StreamingResponse):
    def __init__(
        self,
        factory,
        accepted,
        kwargs,
        *,
        stream_generator=None,
        finisher=finish_student,
        demo_run=None,
    ):
        self.aborted = False
        self.factory, self.run_id = factory, accepted["run_id"]
        self.permit = kwargs.get("permit")
        self.finisher = finisher
        self.demo_run = kwargs["demo_run"] = demo_run
        if demo_run is None:
            cancelled = active_runs[self.run_id] = asyncio.Event()
            # Whoever pops first records an unsent run.
            self.claim = kwargs["claim"] = (
                lambda: active_runs.pop(self.run_id, None) is not None
            )
        else:
            cancelled = demo_run.cancelled
            self.claim = kwargs["claim"] = demo_run.claim
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

    async def demo_abort(self):
        """End a demo run once, whether or not its body ever started."""
        if self.demo_run is None or self.aborted:
            return
        self.aborted = True
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(CLEANUP_SECONDS):
                with suppress(Exception):
                    await self.body_iterator.aclose()
                if self.claim():
                    # Response headers can fail before the generator starts.
                    try:
                        await self.finisher(
                            self.factory,
                            self.run_id,
                            status="interrupted",
                            error_code="disconnected",
                            **demo_kwargs(self.demo_run),
                        )
                    except Exception:
                        logger.error("Unable to record unsent generation")
            if self.permit is not None:
                self.permit.release()
            # §6: done only after the producer and final saves end.
            await self.demo_run.release()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self.demo_run is not None:
                await self.demo_abort()
            else:
                # Disconnect during ASGI send can leave the generator suspended.
                if self.permit is not None:
                    self.permit.release()
                with anyio.CancelScope(shield=True):
                    with anyio.move_on_after(CLEANUP_SECONDS):
                        await self.body_iterator.aclose()
                        if self.claim():
                            # Response headers can fail before the generator starts.
                            try:
                                await self.finisher(
                                    self.factory,
                                    self.run_id,
                                    status="interrupted",
                                    error_code="disconnected",
                                )
                            except Exception:
                                logger.error(
                                    "Unable to record unsent generation"
                                )


async def stream_student(factory, accepted, kwargs, started, cancelled):
    language = kwargs.get("display_language", "ko")
    seq = 0
    partial = ""
    first_output_at = None
    task = reading = None
    queue = asyncio.Queue(maxsize=1)
    permit = kwargs["permit"]
    demo_run = kwargs.get("demo_run")
    claim = kwargs.get("claim") or (
        lambda: active_runs.pop(accepted["run_id"], None)
    )
    terminal = False
    closing = False
    last_sent = started
    cancellation = asyncio.create_task(cancelled.wait())

    def frame(kind, canonical=None, **data):
        nonlocal seq, last_sent
        last_sent = time.monotonic()
        payload = {
            "run_id": accepted["run_id"],
            "turn_id": accepted["turn_id"],
            "seq": seq,
            **data,
        }
        if demo_run is not None:
            payload.update(demo_run.fields(kind, data, canonical))
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
            **demo_kwargs(demo_run),
        )

    async def current():
        # §11: a revoked context gets only a terminal without body text.
        if demo_run is not None and (error := await demo_run.revoked()):
            raise error

    def terminal_frame(result, canonical=None):
        if result["status"] == "completed":
            return frame(
                "output.completed",
                status="completed",
                message=result["message"],
                turn_index=result["turn_index"],
                **provenance_of(result),
            )
        return frame(
            f"run.{result['status']}",
            canonical,
            status=result["status"],
            code=result["error_code"],
            retryable=result["retryable"],
            message=(
                translate("teacher.conversation_ended", language)
                if result["status"] == "cancelled"
                else (
                    translate("teacher.student_configuration_error", language)
                    if result["error_code"] == "configuration_unavailable"
                    else translate("teacher.student_failed", language)
                )
            ),
            **provenance_of(result),
        )

    try:
        await current()
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
            state = (
                await snapshot(db, run, **demo_kwargs(demo_run))
                if run.status != "running"
                else None
            )
        if state is not None:
            await current()
            terminal = True
            yield terminal_frame(state)
            return
        if cancelled.is_set():
            raise StudentStreamError("session_ended")

        async def produce():
            # Reservation hands execution ownership to this response's worker.
            permit.task = asyncio.current_task()
            # The producer can outlive bounded cleanup; it holds its own lease.
            async with demo_run.child() if demo_run else nullcontext() as child:
                request = bind_child(permit, kwargs["request"], child)
                async with aclosing(
                    execute_call(
                        permit,
                        request,
                        kind="stream",
                        run_id=accepted["run_id"],
                        session_id=accepted["session_id"],
                        demo_adapter=kwargs.get("demo_adapter"),
                    )
                ) as events:
                    async for event in events:
                        if closing:
                            return
                        await queue.put(event)

        task = asyncio.create_task(produce())
        if demo_run is not None:
            demo_run.track(task)
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
                await current()
                yield frame("output.delta", text=event.text)
            elif event.type == "completed":
                try:
                    # Some adapters complete with metadata after emitting all text deltas.
                    result = await finish(
                        "completed", content=event.text or partial
                    )
                except DemoError:
                    raise
                except Exception:
                    logger.error("Student final commit failed")
                    raise StudentStreamError("storage_error") from None
                await current()
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
                raise StudentStreamError(
                    code or "provider_error", canonical=event.error_code
                )
    except asyncio.CancelledError:
        raise
    except DemoError as error:
        # The run's DB is retired: nothing is saved there and no body is sent.
        terminal = True
        yield revoked_frame(
            frame, error, translate("teacher.student_failed", language)
        )
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
            await current()
            terminal = True
            yield terminal_frame(
                result,
                (
                    error.canonical
                    if isinstance(error, StudentStreamError)
                    else None
                ),
            )
        except DemoError as error:
            terminal = True
            yield revoked_frame(
                frame, error, translate("teacher.student_failed", language)
            )
        except Exception:
            logger.error("Unable to record student failure")
            yield frame(
                "run.failed",
                status="failed",
                code="storage_error",
                message=translate("teacher.student_storage_failed", language),
                retryable=True,
            )
    finally:
        claim()
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

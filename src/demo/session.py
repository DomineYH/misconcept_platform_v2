"""Demo control orchestration over injected visitor/lifecycle boundaries.

The visitor provider owns all state, locks, admission and receipts. These
protocols are adapter contracts for #191/#192/#194, not alternate providers.
"""

import asyncio
import errno
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Literal, Protocol
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

Role = Literal["admin", "teacher"]
Operation = Literal["start", "role", "reset"]
VisitState = Literal[
    "preparing",
    "prepare_failed",
    "ready",
    "resetting",
    "reset_failed",
    "expired",
    "closing",
]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
CONTRACT_VERSION = "d0-03-v1-r3"
CONTENT_VERSION = "d0-02-v1"
FIXTURE_VERSION = "demo-fixture-v1"

# status, retryable, recovery, fixed Retry-After (dynamic values are supplied).
ERRORS = {
    "SESSION_REQUIRED": (401, False, "start_required", None),
    "EXPIRED": (410, False, "expired", None),
    "STALE_GENERATION": (409, False, "stale_generation", None),
    "STALE_ROLE": (409, False, "stale_role", None),
    "START_IN_PROGRESS": (409, False, "preparing", None),
    "PREPARE_FAILED": (503, True, "prepare_failed", None),
    "RESET_IN_PROGRESS": (409, False, "resetting", None),
    "RESET_FAILED": (503, True, "reset_failed", None),
    "DRAIN_TIMEOUT": (503, True, "reset_failed", None),
    "DB_LOCKED": (503, True, "retry_later", 1),
    "ROLE_DELETED": (409, False, "role_deleted", None),
    "BUSY": (409, True, "wait_or_stop", None),
    "CSRF_INVALID": (403, False, "csrf_refresh", None),
    "LIMIT_REACHED": (429, False, "limit_reached", None),
    "RATE_LIMITED": (429, True, "retry_later", None),
    "CAPACITY": (503, True, "capacity", 30),
    "STORAGE_LIMIT": (507, False, "storage_limit", None),
    "CLEANUP_BACKLOG": (503, True, "retry_later", None),
    "VERSION_MISMATCH": (503, False, "unavailable", None),
    "CONFIGURATION_INVALID": (503, False, "unavailable", None),
    "INVALID_REQUEST": (422, False, "check_input", None),
    "REQUEST_CONFLICT": (409, False, "refresh_state", None),
    "RESOURCE_NOT_FOUND": (404, False, "not_found", None),
}


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartCommand(Schema):
    request_id: str
    role: Role

    @field_validator("request_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("canonical UUID required")
        return value


class RoleCommand(StartCommand):
    expected_generation: PositiveInt
    expected_auth_epoch: PositiveInt


class ResetCommand(Schema):
    request_id: str
    expected_generation: PositiveInt
    expected_auth_epoch: PositiveInt
    confirm: StrictBool

    _uuid = field_validator("request_id")(StartCommand.canonical_uuid.__func__)

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value: bool) -> bool:
        if not value:
            raise ValueError("confirmation required")
        return value


class TourFacts(Schema):
    schema_version: Literal[1] = 1
    browser_scope_id: str
    generation: PositiveInt
    auth_epoch: PositiveInt
    journey_session_id: PositiveInt | None = None
    teacher_entered: bool = False
    scenario_created: bool = False
    dialogue_complete: bool = False
    analysis_ready: bool = False
    teacher_deleted: bool = False
    session_deleted: bool = False


class Provenance(Schema):
    mode: Literal["demo"] = "demo"
    response_source: Literal["prepared_example"] = "prepared_example"
    external_ai_called: Literal[False] = False
    contract_version: str = CONTRACT_VERSION
    content_version: str = CONTENT_VERSION
    fixture_version: str = FIXTURE_VERSION
    record_origin: Literal["seed_prepared", "visitor_generated"]


class DemoState(Schema):
    contract_version: str = CONTRACT_VERSION
    content_version: str = CONTENT_VERSION
    fixture_version: str = FIXTURE_VERSION
    state: VisitState
    browser_scope_id: str
    generation: PositiveInt
    auth_epoch: PositiveInt
    role: Role | None
    expires_at: str
    idle_ttl_seconds: PositiveInt
    absolute_ttl_seconds: PositiveInt
    request_id: str | None = None
    csrf_cookie_name: Literal["demo_csrftoken"] = "demo_csrftoken"
    csrf_header_name: Literal["x-csrf-token"] = "x-csrf-token"
    next_url: str | None
    active: bool
    provenance: Provenance
    last_error: str | None = None
    tour_facts: TourFacts | None = None


class ErrorDetail(Schema):
    code: str
    retryable: bool
    recovery: str
    generation: PositiveInt | None = None
    auth_epoch: PositiveInt | None = None


class ErrorEnvelope(Schema):
    detail: ErrorDetail


class DemoError(Exception):
    def __init__(
        self,
        code: str,
        *,
        generation: int | None = None,
        auth_epoch: int | None = None,
        retry_after: int | None = None,
    ):
        key = code.removeprefix("DEMO_")
        self.status, retryable, recovery, fixed = ERRORS[key]
        self.code = f"DEMO_{key}"
        self.retry_after = fixed if fixed is not None else retry_after
        self.envelope = ErrorEnvelope(
            detail=ErrorDetail(
                code=self.code,
                retryable=retryable,
                recovery=f"demo.{recovery}",
                generation=generation,
                auth_epoch=auth_epoch,
            )
        )
        super().__init__(self.code)


def operation_error(exc: Exception, fallback: str) -> DemoError:
    if isinstance(exc, DemoError):
        return exc
    if isinstance(exc, DBAPIError):
        number = getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF
        if number in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return DemoError("DB_LOCKED")
        if number == sqlite3.SQLITE_FULL:
            return DemoError("STORAGE_LIMIT")
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return DemoError("STORAGE_LIMIT")
    return DemoError(fallback)


class CurrentState(Schema):
    state: VisitState
    generation: PositiveInt
    auth_epoch: PositiveInt
    role: Role | None
    browser_scope_id: str


class ControlReceipt(Schema):
    operation: Operation
    request_id: str
    result: DemoState | ErrorEnvelope
    historical: bool
    current: CurrentState


class OperationToken(Protocol):
    op_id: str
    visitor_id: object
    generation: int
    attempt_no: int
    operation: Literal["start", "reset"]
    request_id: str


class SeedRefs(Protocol):
    admin: int
    teacher: int
    completed_session: int


class GenerationRef(Protocol):
    seed_refs: SeedRefs


class VisitorSnapshot(Protocol):
    browser_scope_id: str
    state: VisitState
    generation: int
    auth_epoch: int
    role: Role | None
    expires_at: datetime
    idle_ttl_seconds: int
    absolute_ttl_seconds: int
    current_ref: GenerationRef | None
    operation: OperationToken | None
    last_error: str | None


class DemoContext(Protocol):
    visitor_id: object
    verified_visitor: object
    browser_scope_id: str
    generation: int
    auth_epoch: int
    role: Role
    user_id: int
    lease: object
    factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class StoredResult:
    operation: Operation
    request_id: str
    result: DemoState | ErrorEnvelope
    browser_scope_id: str
    generation: int
    auth_epoch: int
    status: int = 200
    retry_after: int | None = None


class TransitionHandle(Protocol):
    def snapshot(self) -> VisitorSnapshot: ...
    def check(self, ctx: DemoContext) -> None: ...
    def replay(
        self,
        request_id: str,
        operation: Operation | None = None,
        body: dict | None = None,
    ) -> StoredResult | DemoState | None:
        """Check TTL, ownership/body conflict and receipt before stale guards."""
        ...

    def record(
        self,
        operation: Operation,
        command: Schema,
        result: DemoState | ErrorEnvelope | None,
        status: int,
    ) -> None:
        """Reserve a bounded receipt; reject quota before a role mutation."""
        ...

    def change_role(
        self,
        ctx: DemoContext,
        role: Role,
        busy: bool,
    ) -> VisitorSnapshot: ...
    def mark_reset(
        self,
        ctx: object,
        request_id: str,
    ) -> tuple[OperationToken, object | None]: ...
    def publish(
        self,
        op: OperationToken,
        candidate_ref: object,
    ) -> VisitorSnapshot: ...
    def fail(self, op: OperationToken, code: str) -> bool: ...


@dataclass(frozen=True)
class StartReservation:
    snapshot: VisitorSnapshot
    operation: OperationToken | None = None
    replay: StoredResult | DemoState | None = None


class Visitors(Protocol):
    async def lookup(self, verified_bootstrap: object) -> object: ...
    async def reserve_start(
        self,
        verified_bootstrap: object,
        request_id: str,
        role: Role,
    ) -> StartReservation: ...
    def transition(
        self,
        verified_visitor: object,
    ) -> AbstractAsyncContextManager[TransitionHandle]: ...
    async def clone(self, op: OperationToken) -> object: ...
    async def cancel_preparation(self, op: OperationToken) -> None: ...


class Lifecycle(Protocol):
    def busy_locked(self, handle: TransitionHandle, scope: object) -> bool: ...
    async def cancel_scope(self, scope: object) -> int: ...
    async def drain(
        self, scope: object, timeout_seconds: int
    ) -> "DrainResult": ...


class DrainResult(Protocol):
    drained: bool
    remaining: int


class RequestDB(Protocol):
    def handoff_request_db(self, ctx: object) -> None: ...
    async def release_request_db(self, ctx: object) -> None: ...


@dataclass(frozen=True)
class ControlResult:
    body: DemoState | ControlReceipt
    status: int = 200
    retry_after: int | None = None


def public_state(snapshot: VisitorSnapshot) -> DemoState:
    if snapshot.state in ("expired", "closing"):
        raise DemoError("EXPIRED")
    ready = snapshot.state == "ready"
    role = snapshot.role if ready else None
    query = urlencode(
        {
            "ds": snapshot.browser_scope_id,
            "dg": snapshot.generation,
            "da": snapshot.auth_epoch,
        }
    )
    path = "/admin/scenarios" if role == "admin" else "/scenarios"
    return DemoState(
        state=snapshot.state,
        browser_scope_id=snapshot.browser_scope_id,
        generation=snapshot.generation,
        auth_epoch=snapshot.auth_epoch,
        role=role,
        expires_at=snapshot.expires_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        idle_ttl_seconds=snapshot.idle_ttl_seconds,
        absolute_ttl_seconds=snapshot.absolute_ttl_seconds,
        request_id=(
            snapshot.operation.request_id if snapshot.operation else None
        ),
        next_url=f"{path}?{query}" if ready else None,
        active=snapshot.state in ("preparing", "resetting"),
        provenance=Provenance(record_origin="seed_prepared"),
        last_error=snapshot.last_error,
    )


def replay_result(
    stored: StoredResult | DemoState,
    snapshot: VisitorSnapshot,
) -> ControlResult:
    if isinstance(stored, DemoState):
        return ControlResult(stored, 202)
    current = public_state(snapshot)
    historical = (
        stored.browser_scope_id,
        stored.generation,
        stored.auth_epoch,
    ) != (
        current.browser_scope_id,
        current.generation,
        current.auth_epoch,
    )
    result = stored.result
    if historical and isinstance(result, DemoState):
        result = result.model_copy(update={"next_url": None})
    return ControlResult(
        ControlReceipt(
            operation=stored.operation,
            request_id=stored.request_id,
            result=result,
            historical=historical,
            current=CurrentState(
                **current.model_dump(
                    include={
                        "state",
                        "generation",
                        "auth_epoch",
                        "role",
                        "browser_scope_id",
                    }
                )
            ),
        ),
        stored.status if isinstance(result, ErrorEnvelope) else 200,
        stored.retry_after,
    )


class DemoSession:
    def __init__(
        self,
        visitors: Visitors,
        lifecycle: Lifecycle,
        request_db: RequestDB,
        *,
        drain_seconds: int = 5,
    ):
        if type(drain_seconds) is not int or not 1 <= drain_seconds <= 30:
            raise DemoError("CONFIGURATION_INVALID")
        self.visitors = visitors
        self.lifecycle = lifecycle
        self.request_db = request_db
        self.drain_seconds = drain_seconds
        self._tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    async def state(
        self,
        bootstrap: object,
        request_id: str | None = None,
    ) -> ControlResult:
        visitor = await self.visitors.lookup(bootstrap)
        async with self.visitors.transition(visitor) as handle:
            snapshot = handle.snapshot()
            current = public_state(snapshot)
            if request_id is not None:
                stored = handle.replay(request_id)
                if stored is None:
                    raise DemoError(
                        "RESOURCE_NOT_FOUND",
                        generation=current.generation,
                        auth_epoch=current.auth_epoch,
                    )
                return replay_result(stored, snapshot)
            return ControlResult(current)

    async def start(
        self, bootstrap: object, command: StartCommand
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
        reservation = await self.visitors.reserve_start(
            bootstrap,
            command.request_id,
            command.role,
        )
        if reservation.replay is not None:
            return replay_result(reservation.replay, reservation.snapshot)
        if reservation.operation is None:
            return ControlResult(public_state(reservation.snapshot))
        task = self._own(
            self._prepare(reservation.operation), reservation.operation
        )
        snapshot = await asyncio.shield(task)
        return ControlResult(public_state(snapshot), 201)

    def _own(self, work: Awaitable, op: OperationToken) -> asyncio.Task:
        task = asyncio.create_task(work)
        self._tasks[op.op_id] = task

        def completed(done: asyncio.Task) -> None:
            self._tasks.pop(op.op_id, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        return task

    async def _build(self, op: OperationToken) -> VisitorSnapshot:
        candidate = await self.visitors.clone(op)
        async with self.visitors.transition(op) as handle:
            return handle.publish(op, candidate)

    async def _failed(self, op: OperationToken, exc: Exception) -> DemoError:
        fallback = (
            "PREPARE_FAILED" if op.operation == "start" else "RESET_FAILED"
        )
        error = operation_error(exc, fallback)
        async with self.visitors.transition(op) as handle:
            snapshot = handle.snapshot()
            if snapshot.state in ("expired", "closing"):
                return DemoError("EXPIRED")
            handle.fail(op, error.code)
        return error

    async def _cancelled(self, op: OperationToken) -> None:
        try:
            await self.visitors.cancel_preparation(op)
        finally:
            await self._failed(op, RuntimeError())

    async def _prepare(self, op: OperationToken) -> VisitorSnapshot:
        try:
            return await self._build(op)
        except asyncio.CancelledError:
            await self._cancelled(op)
            raise
        except Exception as exc:
            raise await self._failed(op, exc) from None

    async def _release(self, ctx: object) -> None:
        cleanup = asyncio.create_task(self.request_db.release_request_db(ctx))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def reset(
        self,
        bootstrap: object,
        command: ResetCommand,
        context: Callable[[], Awaitable[object]],
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
        visitor = await self.visitors.lookup(bootstrap)
        async with self.visitors.transition(visitor) as handle:
            stored = handle.replay(
                command.request_id, "reset", command.model_dump()
            )
            if stored is not None:
                return replay_result(stored, handle.snapshot())
        ctx = await context()
        task = None
        released = asyncio.Event()
        try:
            async with self.visitors.transition(visitor) as handle:
                stored = handle.replay(
                    command.request_id, "reset", command.model_dump()
                )
                if stored is not None:
                    return replay_result(stored, handle.snapshot())
                ready = handle.snapshot().state == "ready"
                handle.record("reset", command, None, 0)
                op, scope = handle.mark_reset(ctx, command.request_id)
                handoff_error = None
                if ready:
                    try:
                        self.request_db.handoff_request_db(ctx)
                    except Exception as exc:
                        handoff_error = exc
                # No await is allowed between mark, handoff and Task ownership.
                task = self._own(
                    self._reset(
                        op,
                        scope,
                        ctx if ready else None,
                        released,
                        handoff_error,
                    ),
                    op,
                )
        finally:
            released.set()
            if task is None:
                await self._release(ctx)
        snapshot = await asyncio.shield(task)
        return ControlResult(public_state(snapshot))

    async def _reset(
        self,
        op: OperationToken,
        scope: object | None,
        ctx: object | None,
        released: asyncio.Event,
        handoff_error: Exception | None,
    ) -> VisitorSnapshot:
        try:
            await released.wait()
            if ctx is not None:
                await self._release(ctx)
                ctx = None
            if handoff_error is not None:
                raise handoff_error
            if scope is not None:
                await self.lifecycle.cancel_scope(scope)
                drained = await self.lifecycle.drain(scope, self.drain_seconds)
                if not drained.drained:
                    raise DemoError("DRAIN_TIMEOUT")
            return await self._build(op)
        except asyncio.CancelledError:
            try:
                if ctx is not None:
                    await self._release(ctx)
            finally:
                await self._cancelled(op)
            raise
        except Exception as exc:
            raise await self._failed(op, exc) from None

    async def shutdown(self) -> None:
        self._closing = True
        # Let newly owned Tasks enter their cancellation/cleanup handlers first.
        await asyncio.sleep(0)
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def role(
        self,
        bootstrap: object,
        command: RoleCommand,
        context: Callable[[], Awaitable[DemoContext]],
    ) -> ControlResult:
        visitor = await self.visitors.lookup(bootstrap)
        async with self.visitors.transition(visitor) as handle:
            stored = handle.replay(
                command.request_id, "role", command.model_dump()
            )
            if stored is not None:
                return replay_result(stored, handle.snapshot())
        ctx = await context()
        try:
            async with self.visitors.transition(visitor) as handle:
                stored = handle.replay(
                    command.request_id, "role", command.model_dump()
                )
                if stored is not None:
                    return replay_result(stored, handle.snapshot())
                handle.check(ctx)
                handle.record("role", command, None, 0)
                try:
                    snapshot = handle.snapshot()
                    refs = snapshot.current_ref.seed_refs
                    async with ctx.factory() as db:
                        current = (
                            await db.execute(
                                text('SELECT role FROM "user" WHERE id=:id'),
                                {"id": ctx.user_id},
                            )
                        ).scalar_one_or_none()
                        if current != ctx.role:
                            raise DemoError(
                                "ROLE_DELETED",
                                generation=ctx.generation,
                                auth_epoch=ctx.auth_epoch,
                            )
                        if command.role != ctx.role:
                            if self.lifecycle.busy_locked(
                                handle, (ctx.visitor_id, ctx.generation)
                            ):
                                raise DemoError(
                                    "BUSY",
                                    generation=ctx.generation,
                                    auth_epoch=ctx.auth_epoch,
                                )
                            target = (
                                refs.admin
                                if command.role == "admin"
                                else refs.teacher
                            )
                            exists = (
                                await db.execute(
                                    text(
                                        'SELECT role FROM "user" WHERE id=:id'
                                    ),
                                    {"id": target},
                                )
                            ).scalar_one_or_none()
                            if exists != command.role:
                                raise DemoError(
                                    "ROLE_DELETED",
                                    generation=ctx.generation,
                                    auth_epoch=ctx.auth_epoch,
                                )
                    busy = self.lifecycle.busy_locked(
                        handle, (ctx.visitor_id, ctx.generation)
                    )
                    result = public_state(
                        handle.change_role(ctx, command.role, busy)
                    )
                    result = result.model_copy(
                        update={"request_id": command.request_id}
                    )
                    handle.record("role", command, result, 200)
                    return ControlResult(result)
                except Exception as exc:
                    error = operation_error(exc, "CONFIGURATION_INVALID")
                    handle.record("role", command, error.envelope, error.status)
                    raise error from None
        finally:
            await self.request_db.release_request_db(ctx)

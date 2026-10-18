"""Demo control orchestration over injected visitor/lifecycle boundaries.

The visitor provider owns all state, locks, admission and receipts. These
protocols are adapter contracts for #191/#192/#194, not alternate providers.
"""

import asyncio
import errno
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
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


class TourContent(Protocol):
    first_inputs: frozenset[str]
    common_input: str

    def supported_snapshot(self, row: Mapping[str, object]) -> bool:
        """Validate native immutable snapshot and approved supported hash."""
        ...


class GenerationRef(Protocol):
    seed_refs: SeedRefs
    tour_content: TourContent


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


class ControlContext(Protocol):
    verified_visitor: object
    expected_generation: int
    expected_auth_epoch: int


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
        """With result=None, reserve ID/body/quota without a success receipt.

        Finalize under this same handle after the role mutation or failure.
        """
        ...

    def change_role(
        self,
        ctx: DemoContext,
        role: Role,
        busy: bool,
    ) -> VisitorSnapshot: ...
    def mark_reset(
        self,
        ctx: DemoContext | ControlContext,
        request_id: str,
    ) -> tuple[OperationToken, object | None]: ...
    def publish(
        self,
        op: OperationToken,
        candidate_ref: object,
    ) -> VisitorSnapshot: ...
    def fail(
        self,
        op: OperationToken,
        code: str,
        *,
        retry_after: int | None = None,
    ) -> bool: ...


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
    def handoff_request_db(self, ctx: DemoContext) -> None: ...
    async def release_request_db(
        self, ctx: DemoContext | ControlContext
    ) -> None: ...


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
        (
            DemoError(
                result.detail.code, retry_after=stored.retry_after
            ).retry_after
            if isinstance(result, ErrorEnvelope)
            else stored.retry_after
        ),
    )


_tour_cache: dict[tuple[str, int], TourFacts] = {}
_MILESTONES = (
    "teacher_entered",
    "scenario_created",
    "dialogue_complete",
    "analysis_ready",
    "teacher_deleted",
    "session_deleted",
)


def empty_tour_facts(snapshot: VisitorSnapshot) -> TourFacts:
    return TourFacts(
        browser_scope_id=snapshot.browser_scope_id,
        generation=snapshot.generation,
        auth_epoch=snapshot.auth_epoch,
    )


def discard_tour_facts(handle: TransitionHandle) -> None:
    """#191 expiry and #193 reset call this under the existing handle."""
    scope = handle.snapshot().browser_scope_id
    for key in tuple(_tour_cache):
        if key[0] == scope:
            del _tour_cache[key]


def retain_tour_facts(handle: TransitionHandle, facts: TourFacts) -> TourFacts:
    """Retain proven facts only after the caller's transaction commits."""
    snapshot = handle.snapshot()
    public_state(snapshot)
    if (facts.browser_scope_id, facts.generation) != (
        snapshot.browser_scope_id,
        snapshot.generation,
    ):
        raise DemoError("STALE_GENERATION")
    if facts.auth_epoch != snapshot.auth_epoch:
        raise DemoError("STALE_ROLE")
    key = (facts.browser_scope_id, facts.generation)
    previous = _tour_cache.get(key, empty_tour_facts(snapshot))
    if (
        previous.journey_session_id is not None
        and facts.journey_session_id != previous.journey_session_id
    ):
        raise DemoError("CONFIGURATION_INVALID")
    updates = {
        name: getattr(previous, name) or getattr(facts, name)
        for name in _MILESTONES
    }
    if updates["analysis_ready"]:
        updates["dialogue_complete"] = True
    if updates["dialogue_complete"]:
        updates["scenario_created"] = updates["teacher_entered"] = True
    result = facts.model_copy(update=updates)
    _tour_cache[key] = result
    return result


def remember_teacher_entry(handle: TransitionHandle) -> None:
    snapshot = handle.snapshot()
    if snapshot.role == "teacher":
        key = (snapshot.browser_scope_id, snapshot.generation)
        previous = _tour_cache.get(key, empty_tour_facts(snapshot))
        retain_tour_facts(
            handle,
            previous.model_copy(
                update={
                    "auth_epoch": snapshot.auth_epoch,
                    "teacher_entered": True,
                }
            ),
        )


def captured_facts(facts: TourFacts, updates: dict) -> TourFacts:
    for name in _MILESTONES:
        updates[name] = getattr(facts, name) or updates.get(name, False)
    return facts.model_copy(update=updates)


async def capture_tour_facts(
    ctx: DemoContext,
    handle: TransitionHandle,
) -> TourFacts:
    """Read real rows using the caller's current lease; never reenter/commit.

    Capture is side-effect free, including target selection. The caller must
    retain after a successful read or deletion commit while holding this handle.
    """
    handle.check(ctx)
    snapshot = handle.snapshot()
    key = (snapshot.browser_scope_id, snapshot.generation)
    facts = _tour_cache.get(key, empty_tour_facts(snapshot)).model_copy(
        update={"auth_epoch": snapshot.auth_epoch},
    )
    refs = snapshot.current_ref.seed_refs
    content = snapshot.current_ref.tour_content
    try:
        async with ctx.factory() as db:
            teacher = await db.scalar(
                text('SELECT id FROM "user" WHERE id=:id'), {"id": refs.teacher}
            )
            updates = {"teacher_deleted": teacher is None}
            # A tombstone is checked before touching any dialogue or analysis rows.
            rows = (
                (
                    await db.execute(
                        text(
                            "SELECT id, deleted_at, ended_at, config_snapshot_json, config_hash, "
                            "snapshot_origin, source_scenario_version, snapshot_created_at "
                            'FROM "session" WHERE id != :seed ORDER BY id'
                        ),
                        {"seed": refs.completed_session},
                    )
                )
                .mappings()
                .all()
            )
            if facts.journey_session_id is None:
                target = next(
                    (row for row in rows if content.supported_snapshot(row)),
                    None,
                )
            else:
                target = next(
                    (
                        row
                        for row in rows
                        if row["id"] == facts.journey_session_id
                    ),
                    None,
                )
            if target is None:
                return captured_facts(facts, updates)
            updates["journey_session_id"] = target["id"]
            if target["deleted_at"] is not None:
                updates["session_deleted"] = True
                return captured_facts(facts, updates)
            updates["scenario_created"] = content.supported_snapshot(target)
            if not updates["scenario_created"]:
                return captured_facts(facts, updates)
            messages = (
                (
                    await db.execute(
                        text(
                            "SELECT id, role, content, turn_id, turn_index, generation_run_id "
                            "FROM message WHERE session_id=:sid ORDER BY turn_index, id"
                        ),
                        {"sid": target["id"]},
                    )
                )
                .mappings()
                .all()
            )
            runs = (
                (
                    await db.execute(
                        text(
                            "SELECT id, turn_id, operation, status, result_kind, mentor_trigger "
                            "FROM generation_run WHERE session_id=:sid"
                        ),
                        {"sid": target["id"]},
                    )
                )
                .mappings()
                .all()
            )
            by_id = {run["id"]: run for run in runs}
            completed = []
            for message in messages:
                if message["role"] != "teacher" or message["turn_id"] is None:
                    continue
                for student in messages:
                    if (
                        student["role"] != "student"
                        or student["turn_id"] != message["turn_id"]
                    ):
                        continue
                    run = by_id.get(student["generation_run_id"])
                    if (
                        run
                        and run["operation"] == "student"
                        and run["turn_id"] == message["turn_id"]
                        and run["status"] == "completed"
                        and run["result_kind"] == "message"
                    ):
                        completed.append(message)
                        break
            first = common = None
            for message in completed:
                value = message["content"].strip()
                if first is None and value in content.first_inputs:
                    first = message["turn_id"]
                elif first is not None and value == content.common_input:
                    common = message["turn_id"]
                    break
            coaching = any(
                run["turn_id"] == first
                and run["operation"] == "mentor"
                and run["mentor_trigger"] == "auto"
                and run["status"] == "completed"
                and run["result_kind"] == "message"
                and any(
                    message["role"] == "tutor"
                    and message["turn_id"] == first
                    and message["generation_run_id"] == run["id"]
                    for message in messages
                )
                for run in runs
            )
            no_intervention = any(
                run["turn_id"] == common
                and run["operation"] == "mentor"
                and run["mentor_trigger"] == "auto"
                and run["status"] == "completed"
                and run["result_kind"] == "no_intervention"
                for run in runs
            )
            dialogue = (
                first is not None
                and common is not None
                and coaching
                and no_intervention
            )
            updates["dialogue_complete"] = dialogue
            if dialogue:
                updates["teacher_entered"] = True
            if dialogue and target["ended_at"] is not None:
                reports = (
                    (
                        await db.execute(
                            text(
                                "SELECT report.payload_json, run.id AS run_id "
                                "FROM session_feedback_report AS report "
                                "JOIN generation_run AS run ON run.session_id=report.session_id "
                                "AND run.accepted_report_id=report.id "
                                "AND run.accepted_report_version=report.version "
                                "JOIN session_summary AS summary ON summary.session_id=report.session_id "
                                "WHERE report.session_id=:sid AND report.version=2 "
                                "AND report.status='ok' AND run.operation='analysis' AND run.status='ok'"
                            ),
                            {"sid": target["id"]},
                        )
                    )
                    .mappings()
                    .all()
                )
                for report in reports:
                    try:
                        metadata = json.loads(report["payload_json"])[
                            "metadata"
                        ]
                        if (
                            metadata.get("run_id") == report["run_id"]
                            and metadata.get("outcome") != "no_dialogue"
                        ):
                            updates["analysis_ready"] = True
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        return captured_facts(facts, updates)

    finally:
        handle.check(ctx)


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
        *,
        include_tour: bool = False,
        context: Callable[[], Awaitable[DemoContext]] | None = None,
    ) -> ControlResult:
        visitor = await self.visitors.lookup(bootstrap)
        async with self.visitors.transition(visitor) as handle:
            snapshot = handle.snapshot()
            if snapshot.state in ("expired", "closing"):
                discard_tour_facts(handle)
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
            if not include_tour or snapshot.state != "ready":
                return ControlResult(current)
        if context is None:
            raise DemoError("CONFIGURATION_INVALID")
        ctx = await context()
        try:
            async with self.visitors.transition(visitor) as handle:
                projection = await capture_tour_facts(ctx, handle)
                if self._closing:
                    raise DemoError("CONFIGURATION_INVALID")
                projection = retain_tour_facts(handle, projection)
                current = public_state(handle.snapshot()).model_copy(
                    update={"tour_facts": projection},
                )
                return ControlResult(current)
        except Exception as exc:
            raise operation_error(exc, "CONFIGURATION_INVALID") from None
        finally:
            await self._release(ctx)

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
        if self._closing:
            if reservation.operation is not None:
                await self._cancelled(reservation.operation)
                raise DemoError("PREPARE_FAILED")
            raise DemoError("CONFIGURATION_INVALID")
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
            snapshot = handle.publish(op, candidate)
            remember_teacher_entry(handle)
            return snapshot

    async def _failed(self, op: OperationToken, exc: Exception) -> DemoError:
        fallback = (
            "PREPARE_FAILED" if op.operation == "start" else "RESET_FAILED"
        )
        error = operation_error(exc, fallback)
        async with self.visitors.transition(op) as handle:
            snapshot = handle.snapshot()
            if snapshot.state in ("expired", "closing"):
                return DemoError("EXPIRED")
            handle.fail(op, error.code, retry_after=error.retry_after)
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
        context: Callable[[], Awaitable[DemoContext | ControlContext]],
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
                if self._closing:
                    raise DemoError("CONFIGURATION_INVALID")
                ready = handle.snapshot().state == "ready"
                handle.record("reset", command, None, 0)
                op, scope = handle.mark_reset(ctx, command.request_id)
                discard_tour_facts(handle)
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
        _tour_cache.clear()

    async def role(
        self,
        bootstrap: object,
        command: RoleCommand,
        context: Callable[[], Awaitable[DemoContext]],
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
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
                    if self._closing:
                        raise DemoError("CONFIGURATION_INVALID")
                    busy = self.lifecycle.busy_locked(
                        handle, (ctx.visitor_id, ctx.generation)
                    )
                    result = public_state(
                        handle.change_role(ctx, command.role, busy)
                    )
                    result = result.model_copy(
                        update={"request_id": command.request_id}
                    )
                    remember_teacher_entry(handle)
                    handle.record("role", command, result, 200)
                    return ControlResult(result)
                except Exception as exc:
                    error = operation_error(exc, "CONFIGURATION_INVALID")
                    handle.record("role", command, error.envelope, error.status)
                    raise error from None
        finally:
            await self.request_db.release_request_db(ctx)

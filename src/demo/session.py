"""Demo controls over the authoritative visitor registry and injected services."""

import asyncio
import errno
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timezone
from typing import Annotated, Literal, Protocol
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.demo.database import DemoError
from src.demo.visitors import (
    ControlContext,
    DemoContext,
    OperationToken,
    TransitionHandle,
    VerifiedBootstrap,
    VerifiedVisitor,
    VisitorRegistry,
    VisitorSnapshot,
)
from src.demo.visitors import (
    ControlReceipt as VisitorReceipt,
)

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


class TourContent(Protocol):
    """Verified manifest references and supported content from #195/#197."""

    seed_refs: Mapping[str, int]
    first_inputs: frozenset[str]
    common_input: str

    def supported_snapshot(self, row: Mapping[str, object]) -> bool:
        """Validate native immutable snapshot and approved supported hash."""
        ...


class Lifecycle(Protocol):
    def busy_locked(self, handle: TransitionHandle, scope: object) -> bool: ...
    def cancel_scope(self, scope: object) -> int: ...
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
        idle_ttl_seconds=int(
            (
                snapshot.idle_expires_at - snapshot.last_activity_at
            ).total_seconds()
        ),
        absolute_ttl_seconds=int(
            (snapshot.absolute_expires_at - snapshot.created_at).total_seconds()
        ),
        request_id=(
            snapshot.operation.request_id if snapshot.operation else None
        ),
        next_url=f"{path}?{query}" if ready else None,
        active=snapshot.state in ("preparing", "resetting"),
        provenance=Provenance(record_origin="seed_prepared"),
        last_error=snapshot.last_error,
    )


def replay_result(stored: VisitorReceipt) -> ControlResult:
    current = public_state(stored.current)
    previous = stored.result
    if stored.error_code is not None:
        error = DemoError(
            stored.error_code,
            generation=previous.generation,
            auth_epoch=previous.auth_epoch,
            retry_after=stored.retry_after,
        )
        result = ErrorEnvelope(detail=ErrorDetail(**error.detail))
    else:
        result = public_state(previous).model_copy(
            update={"request_id": stored.request_id}
        )
        if result.active and not stored.historical:
            return ControlResult(result, 202)
        if stored.historical:
            result = result.model_copy(update={"next_url": None})
    return ControlResult(
        ControlReceipt(
            operation=stored.operation,
            request_id=stored.request_id,
            result=result,
            historical=stored.historical,
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
        stored.status_code if stored.error_code is not None else 200,
        stored.retry_after,
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
    content: TourContent,
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
    refs = content.seed_refs
    try:
        async with ctx.factory() as db:
            teacher = await db.scalar(
                text('SELECT id FROM "user" WHERE id=:id'),
                {"id": refs["teacher"]},
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
                        {"seed": refs["completed_session"]},
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
        visitors: VisitorRegistry,
        lifecycle: Lifecycle,
        request_db: RequestDB,
        *,
        tour_content: TourContent,
        drain_seconds: int = 5,
    ):
        if type(drain_seconds) is not int or not 1 <= drain_seconds <= 30:
            raise DemoError("CONFIGURATION_INVALID")
        self.visitors = visitors
        self.lifecycle = lifecycle
        self.request_db = request_db
        self.tour_content = tour_content
        self.drain_seconds = drain_seconds
        self._tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    def _visitor(self, bootstrap: VerifiedBootstrap) -> VerifiedVisitor:
        visitor = self.visitors.visitor_for_bootstrap(bootstrap)
        if visitor is None:
            raise DemoError("SESSION_REQUIRED")
        return visitor

    async def state(
        self,
        bootstrap: VerifiedBootstrap,
        request_id: str | None = None,
        *,
        include_tour: bool = False,
        context: Callable[[], Awaitable[DemoContext]] | None = None,
    ) -> ControlResult:
        visitor = self._visitor(bootstrap)
        async with self.visitors.transition(visitor) as handle:
            snapshot = handle.snapshot()
            if snapshot.state in ("expired", "closing"):
                discard_tour_facts(handle)
            current = public_state(snapshot)
            if request_id is None and (
                not include_tour or snapshot.state != "ready"
            ):
                return ControlResult(current)
        if request_id is not None:
            return replay_result(
                await self.visitors.receipt(visitor, request_id)
            )
        if context is None:
            raise DemoError("CONFIGURATION_INVALID")
        ctx = await context()
        try:
            async with self.visitors.transition(visitor) as handle:
                projection = await capture_tour_facts(
                    ctx, handle, self.tour_content
                )
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
        self, bootstrap: VerifiedBootstrap, command: StartCommand
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
        visitor = self.visitors.visitor_for_bootstrap(bootstrap)
        if visitor is not None:
            try:
                stored = await self.visitors.replay(
                    visitor, "start", command.request_id, (command.role,)
                )
            except DemoError as error:
                if error.code != "DEMO_EXPIRED":
                    raise
            else:
                if stored is not None:
                    return replay_result(stored)
        try:
            reservation = await self.visitors.reserve_start(
                bootstrap, command.request_id, command.role
            )
        except DemoError:
            # Another same-ID leader may have settled after the initial lookup.
            visitor = self.visitors.visitor_for_bootstrap(bootstrap)
            if visitor is not None:
                stored = await self.visitors.replay(
                    visitor, "start", command.request_id, (command.role,)
                )
                if stored is not None:
                    return replay_result(stored)
            raise
        visitor = reservation.verified_visitor
        op = reservation.operation
        if self._closing:
            if reservation.leader:
                await self._cancelled(op, visitor)
                raise DemoError("PREPARE_FAILED")
            raise DemoError("CONFIGURATION_INVALID")
        if not reservation.leader:
            if reservation.historical:
                return replay_result(
                    VisitorReceipt(
                        "start",
                        command.request_id,
                        reservation.snapshot,
                        None,
                        True,
                        await self.visitors.snapshot(visitor),
                    )
                )
            if op and op.request_id == command.request_id:
                return replay_result(
                    await self.visitors.receipt(visitor, command.request_id)
                )
            return ControlResult(
                public_state(reservation.snapshot).model_copy(
                    update={"request_id": command.request_id}
                )
            )
        task = self._own(self._prepare(op, visitor), op)
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

    async def _build(
        self, op: OperationToken, visitor: VerifiedVisitor
    ) -> VisitorSnapshot:
        candidate = await self.visitors.prepare(op)
        async with self.visitors.transition(visitor) as handle:
            snapshot = handle.publish(op, candidate)
            remember_teacher_entry(handle)
            return snapshot

    async def _failed(
        self, op: OperationToken, visitor: VerifiedVisitor, exc: Exception
    ) -> DemoError:
        fallback = (
            "PREPARE_FAILED" if op.operation == "start" else "RESET_FAILED"
        )
        error = operation_error(exc, fallback)
        async with self.visitors.transition(visitor) as handle:
            snapshot = handle.snapshot()
            if snapshot.state in ("expired", "closing"):
                return DemoError("EXPIRED")
            handle.fail(op, error.code, error=error)
        return error

    async def _cancelled(
        self, op: OperationToken, visitor: VerifiedVisitor
    ) -> None:
        try:
            await self.visitors.cancel_preparation(op)
        finally:
            await self._failed(op, visitor, RuntimeError())

    async def _prepare(
        self, op: OperationToken, visitor: VerifiedVisitor
    ) -> VisitorSnapshot:
        try:
            return await self._build(op, visitor)
        except asyncio.CancelledError:
            await self._cancelled(op, visitor)
            raise
        except Exception as exc:
            raise await self._failed(op, visitor, exc) from None

    async def _release(self, ctx: DemoContext | ControlContext) -> None:
        cleanup = asyncio.create_task(self.request_db.release_request_db(ctx))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _replay_after_context_error(
        self,
        visitor: VerifiedVisitor,
        command: RoleCommand | ResetCommand,
        operation: Literal["role", "reset"],
        error: DemoError,
    ) -> ControlResult:
        if error.code not in (
            "DEMO_STALE_GENERATION",
            "DEMO_STALE_ROLE",
            "DEMO_START_IN_PROGRESS",
            "DEMO_RESET_IN_PROGRESS",
            "DEMO_PREPARE_FAILED",
            "DEMO_RESET_FAILED",
            "DEMO_ROLE_DELETED",
        ):
            raise error
        stored = await self.visitors.replay(
            visitor, operation, command.request_id, self._body(command)
        )
        if stored is not None:
            return replay_result(stored)
        raise error

    @staticmethod
    def _body(command: RoleCommand | ResetCommand) -> tuple:
        epochs = (command.expected_generation, command.expected_auth_epoch)
        return (
            (command.role, *epochs)
            if isinstance(command, RoleCommand)
            else epochs
        )

    async def reset(
        self,
        bootstrap: VerifiedBootstrap,
        command: ResetCommand,
        context: Callable[[], Awaitable[DemoContext | ControlContext]],
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
        visitor = self._visitor(bootstrap)
        body = self._body(command)
        stored = await self.visitors.replay(
            visitor, "reset", command.request_id, body
        )
        if stored is not None:
            return replay_result(stored)
        try:
            ctx = await context()
        except DemoError as error:
            return await self._replay_after_context_error(
                visitor, command, "reset", error
            )
        task = None
        released = asyncio.Event()
        try:
            async with self.visitors.transition(visitor) as handle:
                stored = handle.replay("reset", command.request_id, body)
                if stored is not None:
                    return replay_result(stored)
                if self._closing:
                    raise DemoError("CONFIGURATION_INVALID")
                ready = handle.snapshot().state == "ready"
                reservation = handle.mark_reset(ctx, command.request_id)
                if not reservation.leader:
                    return replay_result(
                        handle.replay("reset", command.request_id, body)
                    )
                op, scope = reservation.operation, reservation.retired_scope
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
                        visitor,
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
        visitor: VerifiedVisitor,
        scope: object | None,
        ctx: DemoContext | None,
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
                self.lifecycle.cancel_scope(scope)
                drained = await self.lifecycle.drain(scope, self.drain_seconds)
                if not drained.drained:
                    raise DemoError("DRAIN_TIMEOUT")
            return await self._build(op, visitor)
        except asyncio.CancelledError:
            try:
                if ctx is not None:
                    await self._release(ctx)
            finally:
                await self._cancelled(op, visitor)
            raise
        except Exception as exc:
            raise await self._failed(op, visitor, exc) from None

    async def shutdown(self) -> None:
        self._closing = True
        # Let owned Tasks enter their cancellation and cleanup handlers first.
        await asyncio.sleep(0)
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _tour_cache.clear()

    async def role(
        self,
        bootstrap: VerifiedBootstrap,
        command: RoleCommand,
        context: Callable[[], Awaitable[DemoContext]],
    ) -> ControlResult:
        if self._closing:
            raise DemoError("CONFIGURATION_INVALID")
        visitor = self._visitor(bootstrap)
        body = self._body(command)
        stored = await self.visitors.replay(
            visitor, "role", command.request_id, body
        )
        if stored is not None:
            return replay_result(stored)
        try:
            ctx = await context()
        except DemoError as error:
            return await self._replay_after_context_error(
                visitor, command, "role", error
            )
        try:
            async with self.visitors.transition(visitor) as handle:
                stored = handle.replay("role", command.request_id, body)
                if stored is not None:
                    return replay_result(stored)
                handle.check(ctx)
                try:
                    refs = self.tour_content.seed_refs
                    target = refs[command.role]
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
                            if self.lifecycle.busy_locked(handle, ctx.scope):
                                raise DemoError(
                                    "BUSY",
                                    generation=ctx.generation,
                                    auth_epoch=ctx.auth_epoch,
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
                                target = None
                    if self._closing:
                        raise DemoError("CONFIGURATION_INVALID")
                    busy = self.lifecycle.busy_locked(handle, ctx.scope)
                    snapshot = handle.change_role(
                        ctx,
                        command.role,
                        busy,
                        user_id=target,
                        request_id=command.request_id,
                    )
                    remember_teacher_entry(handle)
                    return ControlResult(
                        public_state(snapshot).model_copy(
                            update={"request_id": command.request_id}
                        )
                    )
                except Exception as exc:
                    error = operation_error(exc, "CONFIGURATION_INVALID")
                    handle.record_role_failure(command.request_id, body, error)
                    raise error from None
        finally:
            await self._release(ctx)

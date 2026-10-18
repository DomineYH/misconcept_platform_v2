"""Single authority for demo admission, generations, leases and transitions.

Consumers inject bound methods into the lifecycle and HTTP layers; this module
never selects a process-global database or reads process configuration.
"""

import asyncio
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID, uuid4

from src.demo.database import DatabaseRoot, DemoError, GenerationRef

Role = Literal["admin", "teacher"]
Scope = tuple[str, int]
VisitStatus = Literal[
    "preparing",
    "prepare_failed",
    "ready",
    "resetting",
    "reset_failed",
    "expired",
    "closing",
]


@dataclass(frozen=True)
class DemoSettings:
    workers: int = 1
    idle_ttl_seconds: int = 1800
    absolute_ttl_seconds: int = 7200
    max_visitors: int = 20
    max_db_bytes: int = 33554432
    max_disk_bytes: int = 1073741824
    drain_seconds: int = 5
    cleanup_interval_seconds: int = 30
    max_retired_generations: int = 40

    def validate(self, *, seed_bytes: int):
        bounds = {
            "workers": (1, 1),
            "idle_ttl_seconds": (300, 7200),
            "absolute_ttl_seconds": (1800, 28800),
            "max_visitors": (1, 1000),
            "max_db_bytes": (1048576, 268435456),
            "max_disk_bytes": (67108864, 10737418240),
            "drain_seconds": (1, 30),
            "cleanup_interval_seconds": (5, 300),
            "max_retired_generations": (1, 2000),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise DemoError("CONFIGURATION_INVALID")
        if (
            type(seed_bytes) is not int
            or seed_bytes < 0
            or seed_bytes > self.max_db_bytes
            or self.max_disk_bytes < seed_bytes + 2 * self.max_db_bytes
            or self.absolute_ttl_seconds < self.idle_ttl_seconds
        ):
            raise DemoError("CONFIGURATION_INVALID")


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self):
        return time.monotonic()

    def utcnow(self):
        return datetime.now(timezone.utc)


class Lifecycle(Protocol):
    """The #192 adapter must include every request/registry/child lease."""

    def cancel_scope(self, scope: Scope) -> int: ...

    async def drain(self, scope: Scope, timeout_seconds: int): ...


@dataclass(frozen=True)
class VerifiedBootstrap:
    bootstrap_nonce: str
    _authority: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedVisitor:
    visitor_id: str
    _authority: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class OperationToken:
    op_id: str
    visitor_id: str
    generation: int
    attempt_no: int
    operation: Literal["start", "reset"]
    request_id: str
    _authority: object = field(repr=False, compare=False)


@dataclass(frozen=True, eq=False)
class Lease:
    lease_id: str
    visitor_id: str
    generation: int
    auth_epoch: int
    _ref: GenerationRef = field(repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def released(self):
        return self._released


@dataclass(frozen=True)
class DemoContext:
    visitor_id: str
    browser_scope_id: str
    generation: int
    auth_epoch: int
    role: Role
    user_id: int
    created_at: datetime
    last_activity_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    expires_at: datetime
    factory: object = field(repr=False, compare=False)
    lease: Lease = field(repr=False, compare=False)
    verified_visitor: VerifiedVisitor = field(repr=False, compare=False)
    app_mode: Literal["demo"] = "demo"

    @property
    def scope(self):
        return (self.visitor_id, self.generation)


@dataclass(frozen=True)
class ControlContext:
    verified_visitor: VerifiedVisitor
    expected_generation: int
    expected_auth_epoch: int


@dataclass(frozen=True)
class VisitorSnapshot:
    visitor_id: str
    browser_scope_id: str
    state: VisitStatus
    generation: int
    auth_epoch: int
    role: Role | None
    created_at: datetime
    last_activity_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    expires_at: datetime
    ref: GenerationRef | None
    operation: OperationToken | None
    last_error: str | None
    csrf_revision: int

    @property
    def scope(self):
        return (self.visitor_id, self.generation)

    @property
    def generation_ref(self):
        return self.ref


@dataclass(frozen=True)
class Reservation:
    verified_visitor: VerifiedVisitor
    snapshot: VisitorSnapshot
    operation: OperationToken | None
    leader: bool
    retired_scope: Scope | None = None
    historical: bool = False


ResetResult = Reservation


@dataclass(frozen=True)
class ControlReceipt:
    operation: str
    request_id: str
    result: VisitorSnapshot
    error_code: str | None
    historical: bool
    current: VisitorSnapshot


@dataclass(frozen=True)
class CleanupResult:
    removed: int
    pending: int
    failure_codes: tuple[str, ...]


@dataclass
class _Receipt:
    operation: str
    body: tuple
    snapshot: VisitorSnapshot
    error_code: str | None = None


@dataclass
class VisitorState:
    verified: VerifiedVisitor
    bootstrap: VerifiedBootstrap
    browser_scope_id: str
    state: VisitStatus
    generation: int
    auth_epoch: int
    reserved_role: Role
    created_at: datetime
    last_activity_at: datetime
    created_mono: float
    last_activity_mono: float
    ref: GenerationRef | None = None
    operation: OperationToken | None = None
    role: Role | None = None
    csrf_revision: int = 0
    last_error: str | None = None
    attempt_no: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    owner: asyncio.Task | None = field(default=None, repr=False)
    leases: dict[str, Lease] = field(default_factory=dict, repr=False)
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    receipts: dict[str, _Receipt] = field(default_factory=dict, repr=False)
    slot_reserved: bool = True
    write_handle: object | None = field(default=None, repr=False)


@dataclass
class _Retired:
    verified: VerifiedVisitor
    ref: GenerationRef
    scope: Scope
    state: str = "queued"
    needs_drain: bool = True


@dataclass
class _Builder:
    op: OperationToken
    task: asyncio.Task
    cancel_event: threading.Event
    candidate: GenerationRef | None = None
    cancelled: bool = False


def _uuid(value):
    if not isinstance(value, str):
        raise DemoError("INVALID_REQUEST")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise DemoError("INVALID_REQUEST") from None
    if parsed.version != 4 or str(parsed) != value:
        raise DemoError("INVALID_REQUEST")
    return value


def _positive_int(value):
    if type(value) is not int or value < 1:
        raise DemoError("INVALID_REQUEST")
    return value


def _role(value):
    if value not in ("admin", "teacher"):
        raise DemoError("INVALID_REQUEST")
    return value


class VisitorRegistry:
    def __init__(
        self,
        root: DatabaseRoot,
        *,
        lifecycle: Lifecycle,
        settings: DemoSettings | None = None,
        clock: Clock | None = None,
    ):
        self.root = root
        self.settings = settings or DemoSettings()
        self.settings.validate(seed_bytes=root.seed_bytes)
        if (
            root.max_db_bytes is not None
            and root.max_db_bytes != self.settings.max_db_bytes
        ):
            raise DemoError("CONFIGURATION_INVALID")
        root.max_db_bytes = self.settings.max_db_bytes
        self.clock = clock or SystemClock()
        self.lifecycle = lifecycle
        self._authority = object()
        self._admission = asyncio.Lock()
        self._bootstraps: dict[str, VerifiedBootstrap] = {}
        self._index: dict[str, VerifiedVisitor] = {}
        self._states: dict[str, VisitorState] = {}
        self._retired: dict[int, _Retired] = {}
        self._builders: dict[str, _Builder] = {}
        self._reservations: dict[str, int] = {}
        self._cancelled: set[str] = set()
        self._cleanup_lock = asyncio.Lock()
        self._closing = False

    @property
    def reserved_visitors(self):
        return sum(state.slot_reserved for state in self._states.values())

    def verify_bootstrap(self, bootstrap_nonce: str) -> VerifiedBootstrap:
        """Trusted bridge called only after #193 verifies the signed bootstrap."""
        if (
            not isinstance(bootstrap_nonce, str)
            or not 32 <= len(bootstrap_nonce) <= 256
            or not bootstrap_nonce.isascii()
        ):
            raise DemoError("INVALID_REQUEST")
        return self._bootstraps.setdefault(
            bootstrap_nonce, VerifiedBootstrap(bootstrap_nonce, self._authority)
        )

    def visitor_for_bootstrap(self, bootstrap: VerifiedBootstrap):
        self._bootstrap(bootstrap)
        return self._index.get(bootstrap.bootstrap_nonce)

    def _bootstrap(self, bootstrap):
        if (
            not isinstance(bootstrap, VerifiedBootstrap)
            or self._bootstraps.get(bootstrap.bootstrap_nonce) is not bootstrap
        ):
            raise DemoError("SESSION_REQUIRED")

    def _state(self, verified):
        if not isinstance(verified, VerifiedVisitor):
            raise DemoError("SESSION_REQUIRED")
        state = self._states.get(verified.visitor_id)
        if state is None or state.verified is not verified:
            raise DemoError("SESSION_REQUIRED")
        return state

    def _snapshot(self, state):
        idle = state.last_activity_at + timedelta(
            seconds=self.settings.idle_ttl_seconds
        )
        absolute = state.created_at + timedelta(
            seconds=self.settings.absolute_ttl_seconds
        )
        return VisitorSnapshot(
            state.verified.visitor_id,
            state.browser_scope_id,
            state.state,
            state.generation,
            state.auth_epoch,
            state.role,
            state.created_at,
            state.last_activity_at,
            idle,
            absolute,
            min(idle, absolute),
            state.ref,
            state.operation,
            state.last_error,
            state.csrf_revision,
        )

    def _due(self, state):
        now = self.clock.monotonic()
        return (
            now >= state.last_activity_mono + self.settings.idle_ttl_seconds
            or now >= state.created_mono + self.settings.absolute_ttl_seconds
        )

    def _error(self, state, code):
        return DemoError(
            code, generation=state.generation, auth_epoch=state.auth_epoch
        )

    def _state_check(self, state, generation=None, epoch=None, scope=None):
        if self._due(state) and state.state not in ("expired", "closing"):
            self._expire(state, "ttl")
        if state.state in ("expired", "closing"):
            raise self._error(state, "EXPIRED")
        if (
            generation is not None
            and generation != state.generation
            or scope is not None
            and scope != state.browser_scope_id
        ):
            raise self._error(state, "STALE_GENERATION")
        errors = {
            "preparing": "START_IN_PROGRESS",
            "prepare_failed": "PREPARE_FAILED",
            "resetting": "RESET_IN_PROGRESS",
            "reset_failed": "RESET_FAILED",
        }
        if state.state != "ready":
            raise self._error(state, errors[state.state])
        if epoch is not None and epoch != state.auth_epoch:
            raise self._error(state, "STALE_ROLE")

    def _retire(self, verified, ref, *, needs_drain=True):
        if ref is None:
            return
        ref.factory.retire()
        self._retired.setdefault(
            id(ref),
            _Retired(
                verified,
                ref,
                (ref.visitor_id, ref.generation),
                needs_drain=needs_drain,
            ),
        )

    def _expire(self, state, reason):
        if reason not in ("ttl", "shutdown", "version_replace"):
            raise DemoError("INVALID_REQUEST")
        if state.state in ("expired", "closing"):
            return None
        scope = (state.verified.visitor_id, state.generation)
        self._retire(state.verified, state.ref)
        state.ref = None
        if state.operation is not None:
            self._cancelled.add(state.operation.op_id)
        state.state = "closing" if reason == "shutdown" else "expired"
        state.generation += 1
        state.auth_epoch += 1
        state.role = None
        state.changed.set()
        return scope

    def _backlog(self):
        if len(self._retired) >= self.settings.max_retired_generations:
            raise DemoError(
                "CLEANUP_BACKLOG",
                retry_after=self.settings.cleanup_interval_seconds,
            )

    def _budget(self, *, additional=0, ref=None):
        if ref is not None and (
            self.root.generation_bytes(ref) > self.settings.max_db_bytes
        ):
            raise DemoError("STORAGE_LIMIT")
        if (
            self.root.bytes_used()
            + self.root.external_seed_bytes
            + sum(self._reservations.values())
            + additional
            > self.settings.max_disk_bytes
        ):
            raise DemoError("STORAGE_LIMIT")

    def _reserve_bytes(self, op):
        self._backlog()
        self._budget(additional=self.settings.max_db_bytes)
        self._reservations[op.op_id] = self.settings.max_db_bytes

    def _receipt(self, state, request_id, operation, body):
        receipt = state.receipts.get(request_id)
        if receipt is not None and (
            receipt.operation != operation or receipt.body != body
        ):
            raise self._error(state, "REQUEST_CONFLICT")
        return receipt

    def _result(self, state, *, leader=False, scope=None, receipt=None):
        snapshot = receipt.snapshot if receipt else self._snapshot(state)
        if receipt and receipt.error_code:
            raise self._error(state, receipt.error_code)
        current = self._snapshot(state)
        historical = (
            snapshot.visitor_id,
            snapshot.browser_scope_id,
            snapshot.generation,
            snapshot.auth_epoch,
        ) != (
            current.visitor_id,
            current.browser_scope_id,
            current.generation,
            current.auth_epoch,
        )
        return Reservation(
            state.verified,
            snapshot,
            snapshot.operation,
            leader,
            scope,
            historical,
        )

    def _new_op(
        self, state, request_id, operation, body, *, generation=None, epoch=None
    ):
        if len(state.receipts) >= 256:
            raise self._error(state, "LIMIT_REACHED")
        op = OperationToken(
            str(uuid4()),
            state.verified.visitor_id,
            state.generation if generation is None else generation,
            state.attempt_no + 1,
            operation,
            request_id,
            self._authority,
        )
        self._reserve_bytes(op)
        if generation is not None:
            state.generation = generation
            state.auth_epoch = epoch
        state.attempt_no = op.attempt_no
        state.operation = op
        state.state = "preparing" if operation == "start" else "resetting"
        state.ref = None
        state.role = None
        state.last_error = None
        state.receipts[request_id] = _Receipt(
            operation, body, self._snapshot(state)
        )
        return op

    async def reserve_start(self, verified_bootstrap, request_id, role):
        self._bootstrap(verified_bootstrap)
        _uuid(request_id)
        _role(role)
        async with self._admission:
            if self._closing:
                raise DemoError("EXPIRED")
            verified = self._index.get(verified_bootstrap.bootstrap_nonce)
            if verified is not None:
                async with self.transition(verified) as handle:
                    state = handle._state
                    if self._due(state):
                        handle.expire("ttl")
                    receipt = self._receipt(state, request_id, "start", (role,))
                    if state.state not in ("expired", "closing"):
                        if receipt:
                            return self._result(state, receipt=receipt)
                        if state.state in ("preparing", "resetting"):
                            code = (
                                "START_IN_PROGRESS"
                                if state.state == "preparing"
                                else "RESET_IN_PROGRESS"
                            )
                            raise self._error(state, code)
                        if state.state == "reset_failed":
                            raise self._error(state, "RESET_FAILED")
                        if state.state == "ready":
                            if len(state.receipts) >= 256:
                                raise self._error(state, "LIMIT_REACHED")
                            state.receipts[request_id] = _Receipt(
                                "start", (role,), handle.snapshot()
                            )
                            return self._result(state)
                        self._new_op(state, request_id, "start", (role,))
                        state.reserved_role = role
                        return self._result(state, leader=True)
                    if receipt:
                        raise self._error(state, "EXPIRED")
            self._backlog()
            if self.reserved_visitors >= self.settings.max_visitors:
                raise DemoError("CAPACITY")
            visitor_id = str(uuid4())
            verified = VerifiedVisitor(visitor_id, self._authority)
            now = self.clock.utcnow()
            if now.tzinfo is None or now.utcoffset() != timedelta(0):
                raise DemoError("CONFIGURATION_INVALID")
            mono = self.clock.monotonic()
            state = VisitorState(
                verified,
                verified_bootstrap,
                secrets.token_hex(16),
                "preparing",
                1,
                1,
                role,
                now,
                now,
                mono,
                mono,
            )
            self._new_op(state, request_id, "start", (role,))
            self._states[visitor_id] = state
            self._index[verified_bootstrap.bootstrap_nonce] = verified
            return self._result(state, leader=True)

    @asynccontextmanager
    async def transition(self, verified_visitor):
        state = self._state(verified_visitor)
        task = asyncio.current_task()
        if state.owner is task:
            raise DemoError("INVALID_REQUEST")
        async with state.lock:
            state.owner = task
            handle = TransitionHandle(self, state, task)
            try:
                yield handle
            finally:
                handle._active = False
                state.owner = None

    async def snapshot(self, verified_visitor):
        async with self.transition(verified_visitor) as handle:
            return handle.snapshot()

    async def resolve_visit(self, verified_visitor, expected_generation):
        _positive_int(expected_generation)
        async with self.transition(verified_visitor) as handle:
            self._state_check(handle._state, expected_generation)
            return handle._state.ref

    async def acquire_lease(self, ref, expected_auth_epoch):
        _positive_int(expected_auth_epoch)
        if not self.root.owns(ref):
            raise DemoError("INVALID_REQUEST")
        state = self._states.get(ref.visitor_id)
        if state is None:
            raise DemoError("SESSION_REQUIRED")
        async with self.transition(state.verified) as handle:
            self._state_check(state, ref.generation, expected_auth_epoch)
            if state.ref is not ref:
                raise self._error(state, "STALE_GENERATION")
            return handle._lease(ref, expected_auth_epoch)

    async def retain_lease(self, lease, *, app_mode="demo"):
        if lease is None and app_mode == "normal":
            return None
        state = self._lease_state(lease)
        async with self.transition(state.verified) as handle:
            return handle.retain_lease(lease)

    async def release_lease(self, lease, *, app_mode="demo"):
        if lease is None and app_mode == "normal":
            return None
        state = self._lease_state(lease)
        async with self.transition(state.verified) as handle:
            return handle.release_lease(lease)

    def _lease_state(self, lease):
        if not isinstance(lease, Lease):
            raise DemoError("INVALID_REQUEST")
        state = self._states.get(lease.visitor_id)
        if state is None or state.leases.get(lease.lease_id) is not lease:
            raise DemoError("INVALID_REQUEST")
        return state

    def lease_count(self, scope):
        state = self._states.get(scope[0])
        if state is None:
            return 0
        return sum(
            not lease.released and lease.generation == scope[1]
            for lease in state.leases.values()
        )

    async def wait_for_leases(self, scope, timeout_seconds):
        state = self._states.get(scope[0])
        if state is None:
            return True

        async def wait():
            while self.lease_count(scope):
                state.changed.clear()
                if self.lease_count(scope):
                    await state.changed.wait()

        try:
            await asyncio.wait_for(wait(), timeout_seconds)
            return True
        except TimeoutError:
            return False

    async def make_context(self, verified_visitor, lease, *, user_id):
        """Bind the User already loaded/validated by #193/#194 under this lease."""
        async with self.transition(verified_visitor) as handle:
            return handle.context(lease, user_id=user_id)

    async def check(self, ctx):
        async with self.transition(ctx.verified_visitor) as handle:
            return handle.check(ctx)

    @asynccontextmanager
    async def write_guard(self, ctx):
        async with self.transition(ctx.verified_visitor) as handle:
            handle.check(ctx)
            reservation_id = str(uuid4())
            self._budget(
                ref=handle._state.ref, additional=self.settings.max_db_bytes
            )
            self._reservations[reservation_id] = self.settings.max_db_bytes
            handle._write_ctx = ctx
            handle._state.write_handle = handle
            try:
                yield handle
                handle.check(ctx)
                self._budget(ref=handle._state.ref)
            finally:
                handle._write_ctx = None
                handle._state.write_handle = None
                self._reservations.pop(reservation_id, None)

    def _write_check(self, ref):
        state = self._states[ref.visitor_id]
        handle = getattr(state, "write_handle", None)
        if handle is None or handle._owner is not asyncio.current_task():
            raise self._error(state, "INVALID_REQUEST")
        if (
            handle._write_ctx is None
            or handle._write_ctx.factory is not ref.factory
        ):
            raise self._error(state, "STALE_GENERATION")
        handle.check(handle._write_ctx)
        self._budget(ref=ref)

    async def reset_visit(self, ctx, request_id):
        verified = (
            ctx.verified_visitor
            if isinstance(ctx, (DemoContext, ControlContext))
            else None
        )
        async with self.transition(verified) as handle:
            return handle.mark_reset(ctx, request_id)

    async def expire_visit(self, verified_visitor, reason="ttl"):
        async with self.transition(verified_visitor) as handle:
            scope = handle.expire(reason)
        if scope is not None:
            self.lifecycle.cancel_scope(scope)
        return scope

    async def touch(self, ctx):
        """Only successful foreground data handlers call this method."""
        async with self.transition(ctx.verified_visitor) as handle:
            handle.check(ctx)
            state = handle._state
            mono = self.clock.monotonic()
            state.last_activity_mono = mono
            state.last_activity_at = state.created_at + timedelta(
                seconds=mono - state.created_mono
            )
            return handle.snapshot()

    async def receipt(self, verified_visitor, request_id):
        _uuid(request_id)
        async with self.transition(verified_visitor) as handle:
            state = handle._state
            if self._due(state):
                handle.expire("ttl")
            if state.state in ("expired", "closing"):
                raise self._error(state, "EXPIRED")
            receipt = state.receipts.get(request_id)
            if receipt is None:
                raise DemoError("RESOURCE_NOT_FOUND")
            current = handle.snapshot()
            previous = receipt.snapshot
            historical = (
                previous.browser_scope_id,
                previous.generation,
                previous.auth_epoch,
            ) != (
                current.browser_scope_id,
                current.generation,
                current.auth_epoch,
            )
            return ControlReceipt(
                receipt.operation,
                request_id,
                previous,
                receipt.error_code,
                historical,
                current,
            )

    def _valid_op(self, state, op):
        return (
            isinstance(op, OperationToken)
            and op._authority is self._authority
            and state.operation is op
            and op.visitor_id == state.verified.visitor_id
            and op.generation == state.generation
            and op.attempt_no == state.attempt_no
            and op.op_id not in self._cancelled
            and state.state in ("preparing", "resetting")
        )

    async def prepare(self, op):
        """Clone outside transition; cancellation waits for the SQLite builder."""
        state = self._states.get(getattr(op, "visitor_id", None))
        if state is None:
            raise DemoError("INVALID_REQUEST")
        async with self.transition(state.verified) as handle:
            if self._due(state):
                handle.expire("ttl")
            if state.state in ("expired", "closing"):
                raise self._error(state, "EXPIRED")
            if not self._valid_op(state, op):
                raise self._error(state, "STALE_GENERATION")
            scopes = (
                {
                    item.scope
                    for item in self._retired.values()
                    if item.verified is state.verified
                    and item.needs_drain
                    and item.ref.generation < op.generation
                    and not item.ref.factory.disposed
                }
                if op.operation == "reset"
                else set()
            )
        for scope in scopes:
            await self.drain_retired(scope)
        async with self.transition(state.verified) as handle:
            if self._due(state):
                handle.expire("ttl")
            if not self._valid_op(state, op):
                raise self._error(
                    state,
                    (
                        "EXPIRED"
                        if state.state in ("expired", "closing")
                        else "STALE_GENERATION"
                    ),
                )
            builder = self._builders.get(op.op_id)
            if builder is None:
                cancel_event = threading.Event()
                task = asyncio.create_task(
                    self.root.clone(
                        op.visitor_id,
                        op.generation,
                        operation_id=op.op_id,
                        cancel_event=cancel_event,
                    )
                )
                builder = _Builder(op, task, cancel_event)
                self._builders[op.op_id] = builder
        try:
            candidate = await asyncio.shield(builder.task)
        except asyncio.CancelledError:
            await self.cancel_preparation(op)
            raise
        except Exception:
            async with self.transition(state.verified):
                self._reservations.pop(op.op_id, None)
            raise
        async with self.transition(state.verified) as handle:
            builder.candidate = candidate
            if self._due(state):
                handle.expire("ttl")
            if not self._valid_op(state, op) or builder.cancelled:
                if candidate is not state.ref:
                    self._retire(state.verified, candidate, needs_drain=False)
                self._reservations.pop(op.op_id, None)
                code = (
                    "EXPIRED"
                    if state.state in ("expired", "closing")
                    else "STALE_GENERATION"
                )
                raise self._error(state, code)
            return candidate

    async def cancel_preparation(self, op):
        state = self._states.get(getattr(op, "visitor_id", None))
        if (
            state is None
            or getattr(op, "_authority", None) is not self._authority
        ):
            raise DemoError("INVALID_REQUEST")
        async with self.transition(state.verified) as handle:
            builder = self._builders.get(op.op_id)
            if state.state == "ready" and state.operation is op:
                return
            if self._valid_op(state, op):
                handle.fail(
                    op,
                    (
                        "PREPARE_FAILED"
                        if op.operation == "start"
                        else "RESET_FAILED"
                    ),
                )
            self._cancelled.add(op.op_id)
            if builder is None:
                self._reservations.pop(op.op_id, None)
                return
            builder.cancelled = True
            builder.cancel_event.set()
        cancelled = False
        while not builder.task.done():
            try:
                await asyncio.shield(builder.task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        try:
            candidate = builder.task.result()
        except (Exception, asyncio.CancelledError):
            candidate = None
        # No await between settlement and ownership transfer: repeated caller
        # cancellation cannot orphan a completed SQLite backup.
        if candidate is not None and candidate is not state.ref:
            builder.candidate = candidate
            self._retire(state.verified, candidate, needs_drain=False)
        self._reservations.pop(op.op_id, None)
        if cancelled:
            raise asyncio.CancelledError

    async def create_visit(self, verified_bootstrap, request_id, role):
        reservation = await self.reserve_start(
            verified_bootstrap, request_id, role
        )
        if not reservation.leader:
            return reservation
        op = reservation.operation
        try:
            candidate = await self.prepare(op)
            async with self.transition(reservation.verified_visitor) as handle:
                snapshot = handle.publish(op, candidate)
            return replace(reservation, snapshot=snapshot, leader=False)
        except Exception as error:
            code = (
                error.code if isinstance(error, DemoError) else "PREPARE_FAILED"
            )
            async with self.transition(reservation.verified_visitor) as handle:
                handle.fail(op, code)
            raise DemoError(code) from None

    async def drain_retired(self, scope):
        self.lifecycle.cancel_scope(scope)
        result = await self.lifecycle.drain(scope, self.settings.drain_seconds)
        drained = (
            result["drained"] if isinstance(result, dict) else result.drained
        )
        if not drained or self.lease_count(scope):
            raise DemoError("DRAIN_TIMEOUT")
        for item in list(self._retired.values()):
            if item.scope == scope and item.needs_drain:
                await item.ref.factory.dispose()

    async def cleanup_retired(self):
        async with self._cleanup_lock:
            codes = []
            removed = 0
            for key, retired in list(self._retired.items()):
                retired.state = "draining"
                try:
                    if retired.needs_drain:
                        await self.drain_retired(retired.scope)
                    async with self.transition(retired.verified):
                        if self.lease_count(retired.scope):
                            raise DemoError("DRAIN_TIMEOUT")
                    # Retired generations cannot acquire a new lease after this check.
                    if not await self.root.cleanup(retired.ref):
                        raise DemoError("DB_LOCKED")
                except Exception as error:
                    code = (
                        error.code
                        if isinstance(error, DemoError)
                        else "DEMO_DB_LOCKED"
                    )
                    retired.state = (
                        "locked" if code == "DEMO_DB_LOCKED" else "queued"
                    )
                    codes.append(code)
                    continue
                self._retired.pop(key, None)
                removed += 1
            for state in self._states.values():
                if state.state not in ("expired", "closing"):
                    continue
                pending = any(
                    item.verified is state.verified
                    for item in self._retired.values()
                ) or any(
                    builder.op.visitor_id == state.verified.visitor_id
                    and not builder.task.done()
                    for builder in self._builders.values()
                )
                if (
                    not pending
                    and not any(
                        not lease.released for lease in state.leases.values()
                    )
                    and not any(
                        op_id in self._reservations
                        for op_id in [state.operation.op_id]
                        if state.operation
                    )
                ):
                    state.slot_reserved = False
            return CleanupResult(removed, len(self._retired), tuple(codes))

    async def cleanup_tick(self):
        for state in list(self._states.values()):
            if self._due(state):
                await self.expire_visit(state.verified, "ttl")
                if state.operation is not None:
                    await self.cancel_preparation(state.operation)
        return await self.cleanup_retired()

    async def shutdown(self):
        self._closing = True
        for state in list(self._states.values()):
            await self.expire_visit(state.verified, "shutdown")
            if state.operation is not None:
                await self.cancel_preparation(state.operation)
        return await self.cleanup_retired()


class TransitionHandle:
    def __init__(self, registry, state, owner):
        self._registry = registry
        self._state = state
        self._owner = owner
        self._active = True
        self._write_ctx = None

    def _require(self):
        if (
            not self._active
            or self._owner is not asyncio.current_task()
            or self._state.owner is not self._owner
        ):
            raise DemoError("INVALID_REQUEST")

    @property
    def verified_visitor(self):
        self._require()
        return self._state.verified

    @property
    def scope(self):
        self._require()
        return (self._state.verified.visitor_id, self._state.generation)

    def snapshot(self):
        self._require()
        if self._registry._due(self._state):
            self._registry._expire(self._state, "ttl")
        return self._registry._snapshot(self._state)

    def check(self, ctx):
        self._require()
        if not isinstance(ctx, DemoContext):
            raise DemoError("INVALID_REQUEST")
        state = self._state
        if ctx.verified_visitor is not state.verified:
            raise DemoError("INVALID_REQUEST")
        self._registry._state_check(
            state, ctx.generation, ctx.auth_epoch, ctx.browser_scope_id
        )
        lease = ctx.lease
        if (
            self._registry._lease_state(lease) is not state
            or lease.released
            or lease.generation != ctx.generation
            or lease.auth_epoch != ctx.auth_epoch
            or ctx.factory is not state.ref.factory
            or lease._ref is not state.ref
            or ctx.visitor_id != state.verified.visitor_id
        ):
            raise self._registry._error(state, "INVALID_REQUEST")
        return self.snapshot()

    def _lease(self, ref, epoch):
        self._require()
        lease = Lease(
            str(uuid4()),
            self._state.verified.visitor_id,
            ref.generation,
            epoch,
            ref,
        )
        self._state.leases[lease.lease_id] = lease
        return lease

    def acquire_lease(self, ref, expected_auth_epoch):
        self._require()
        _positive_int(expected_auth_epoch)
        self._registry._state_check(
            self._state, ref.generation, expected_auth_epoch
        )
        if self._state.ref is not ref:
            raise DemoError("INVALID_REQUEST")
        return self._lease(ref, expected_auth_epoch)

    def retain_lease(self, lease):
        self._require()
        if self._registry._lease_state(lease) is not self._state:
            raise DemoError("INVALID_REQUEST")
        self._registry._state_check(
            self._state, lease.generation, lease.auth_epoch
        )
        if lease.released or lease._ref is not self._state.ref:
            raise DemoError("INVALID_REQUEST")
        return self._lease(lease._ref, lease.auth_epoch)

    def release_lease(self, lease):
        self._require()
        if self._registry._lease_state(lease) is not self._state:
            raise DemoError("INVALID_REQUEST")
        if lease.released:
            return False
        object.__setattr__(lease, "_released", True)
        self._state.changed.set()
        return True

    def context(self, lease, *, user_id):
        self._require()
        _positive_int(user_id)
        self._registry._state_check(
            self._state, lease.generation, lease.auth_epoch
        )
        if (
            self._registry._lease_state(lease) is not self._state
            or lease.released
        ):
            raise DemoError("INVALID_REQUEST")
        snapshot = self.snapshot()
        return DemoContext(
            snapshot.visitor_id,
            snapshot.browser_scope_id,
            snapshot.generation,
            snapshot.auth_epoch,
            snapshot.role,
            user_id,
            snapshot.created_at,
            snapshot.last_activity_at,
            snapshot.idle_expires_at,
            snapshot.absolute_expires_at,
            snapshot.expires_at,
            snapshot.ref.factory,
            lease,
            self._state.verified,
        )

    def mark_reset(self, ctx, request_id):
        self._require()
        _uuid(request_id)
        state = self._state
        registry = self._registry
        if not isinstance(ctx, (DemoContext, ControlContext)):
            raise DemoError("INVALID_REQUEST")
        if ctx.verified_visitor is not state.verified:
            raise DemoError("INVALID_REQUEST")
        generation, epoch = (
            (ctx.generation, ctx.auth_epoch)
            if isinstance(ctx, DemoContext)
            else (ctx.expected_generation, ctx.expected_auth_epoch)
        )
        _positive_int(generation)
        _positive_int(epoch)
        if registry._due(state):
            self.expire("ttl")
        if state.state in ("expired", "closing"):
            raise registry._error(state, "EXPIRED")
        body = (generation, epoch)
        receipt = registry._receipt(state, request_id, "reset", body)
        if receipt is not None:
            return registry._result(state, receipt=receipt)
        if state.state == "resetting":
            raise registry._error(state, "RESET_IN_PROGRESS")
        retry = state.state == "reset_failed"
        if retry:
            if generation != state.generation:
                raise registry._error(state, "STALE_GENERATION")
            if epoch != state.auth_epoch:
                raise registry._error(state, "STALE_ROLE")
            if not isinstance(ctx, ControlContext):
                raise DemoError("INVALID_REQUEST")
        else:
            self.check(ctx)
        if len(state.receipts) >= 256:
            raise registry._error(state, "LIMIT_REACHED")
        registry._backlog()
        registry._budget(additional=registry.settings.max_db_bytes)
        scope = (
            (state.verified.visitor_id, state.generation - 1)
            if retry
            else (state.verified.visitor_id, state.generation)
        )
        old = state.ref
        registry._new_op(
            state,
            request_id,
            "reset",
            body,
            generation=state.generation if retry else state.generation + 1,
            epoch=state.auth_epoch if retry else state.auth_epoch + 1,
        )
        if not retry:
            registry._retire(state.verified, old)
        state.changed.set()
        return registry._result(state, leader=True, scope=scope)

    def publish(self, op, candidate_ref):
        self._require()
        registry, state = self._registry, self._state
        try:
            if registry._due(state):
                self.expire("ttl")
            if state.state in ("expired", "closing"):
                raise registry._error(state, "EXPIRED")
            if not registry._valid_op(state, op):
                raise registry._error(state, "STALE_GENERATION")
            if (
                not registry.root.owns(candidate_ref)
                or candidate_ref.visitor_id != op.visitor_id
                or candidate_ref.generation != op.generation
                or candidate_ref.manifest_sha256
                != registry.root.seed_manifest_sha256
            ):
                raise registry._error(state, "VERSION_MISMATCH")
            if op.operation == "reset" and (
                any(
                    registry.lease_count(item.scope)
                    or not item.ref.factory.disposed
                    for item in registry._retired.values()
                    if item.verified is state.verified
                    and item.needs_drain
                    and item.ref.generation < op.generation
                )
            ):
                raise registry._error(state, "DRAIN_TIMEOUT")
            registry._budget(ref=candidate_ref)
        except DemoError:
            if (
                registry.root.owns(candidate_ref)
                and candidate_ref is not state.ref
            ):
                registry._retire(
                    state.verified, candidate_ref, needs_drain=False
                )
            raise
        candidate_ref.factory.set_write_check(
            lambda: registry._write_check(candidate_ref)
        )
        state.ref = candidate_ref
        state.state = "ready"
        state.role = state.reserved_role if op.operation == "start" else "admin"
        state.last_error = None
        state.csrf_revision += 1
        registry._reservations.pop(op.op_id, None)
        snapshot = self.snapshot()
        state.receipts[op.request_id].snapshot = snapshot
        return snapshot

    def fail(self, op, code):
        self._require()
        registry, state = self._registry, self._state
        if not registry._valid_op(state, op):
            return False
        error = registry._error(state, code)
        state.ref = None
        state.role = None
        state.state = (
            "prepare_failed" if op.operation == "start" else "reset_failed"
        )
        state.last_error = error.code
        receipt = state.receipts[op.request_id]
        receipt.error_code = error.code
        receipt.snapshot = self.snapshot()
        builder = registry._builders.get(op.op_id)
        if builder is None or builder.task.done():
            if builder is not None and builder.candidate is not None:
                registry._retire(
                    state.verified, builder.candidate, needs_drain=False
                )
            registry._reservations.pop(op.op_id, None)
        return True

    def expire(self, reason="ttl"):
        self._require()
        return self._registry._expire(self._state, reason)

    def change_role(self, ctx, role, busy, *, user_id=None):
        """#193 checks both current and target User under this same handle."""
        self.check(ctx)
        _role(role)
        if type(busy) is not bool:
            raise DemoError("INVALID_REQUEST")
        state = self._state
        if role == state.role:
            return self.snapshot()
        if busy:
            raise self._registry._error(state, "BUSY")
        if user_id is None:
            raise self._registry._error(state, "ROLE_DELETED")
        _positive_int(user_id)
        state.role = role
        state.auth_epoch += 1
        state.csrf_revision += 1
        return self.snapshot()


# The same operations can be imported and injected without a global registry.
create_visit = VisitorRegistry.create_visit
reserve_start = VisitorRegistry.reserve_start
resolve_visit = VisitorRegistry.resolve_visit
transition = VisitorRegistry.transition
acquire_lease = VisitorRegistry.acquire_lease
retain_lease = VisitorRegistry.retain_lease
release_lease = VisitorRegistry.release_lease
write_guard = VisitorRegistry.write_guard
reset_visit = VisitorRegistry.reset_visit
expire_visit = VisitorRegistry.expire_visit
cancel_preparation = VisitorRegistry.cancel_preparation
cleanup_retired = VisitorRegistry.cleanup_retired

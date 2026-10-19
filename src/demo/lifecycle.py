"""Scoped execution ownership; visitor state and leases belong to #191."""

import asyncio
from collections.abc import Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Literal
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

import anyio

from src.demo.visitors import (
    DemoContext,
    DemoError,
    Lease,
    TransitionHandle,
    VerifiedVisitor,
    VisitorRegistry,
)

ExecutionScope = tuple[UUID | str, int]
ExecutionKey = tuple[ExecutionScope, str, int | str | UUID]
NORMAL_SCOPE: ExecutionScope = ("normal", 0)
KINDS = frozenset(
    {"run", "analysis", "probe", "connection", "session", "comparison"}
)


@dataclass(frozen=True)
class NormalContext:
    factory: object = None
    app_mode: Literal["normal"] = "normal"
    scope: ExecutionScope = NORMAL_SCOPE
    auth_epoch: int = 0
    lease: None = None


@dataclass(frozen=True)
class TaskControl:
    request_cancel: Callable[[], None]
    done: asyncio.Event


@dataclass(frozen=True)
class ExecutionToken:
    key: ExecutionKey
    captured_auth_epoch: int
    task_control: TaskControl
    registry_lease: Lease | None
    verified_visitor: VerifiedVisitor | None = None
    token_id: UUID = field(default_factory=uuid4)
    _cancel_requested: bool = field(
        default=False, init=False, repr=False, compare=False
    )

    @property
    def completed(self):
        return self.task_control.done.is_set()

    def request_cancel(self):
        if not self._cancel_requested:
            object.__setattr__(self, "_cancel_requested", True)
            self.task_control.request_cancel()

    async def wait_done(self):
        await self.task_control.done.wait()


def _error(code, *, scope=None, auth_epoch=None):
    raise DemoError(
        code,
        generation=scope[1] if scope and scope != NORMAL_SCOPE else None,
        auth_epoch=auth_epoch,
    )


def validate_scope(scope):
    if not isinstance(scope, tuple) or len(scope) != 2:
        _error("DEMO_INVALID_REQUEST")
    visitor, generation = scope
    if scope == NORMAL_SCOPE and type(generation) is int:
        return scope
    try:
        identifier = visitor if isinstance(visitor, UUID) else UUID(visitor)
    except (ValueError, TypeError, AttributeError):
        _error("DEMO_INVALID_REQUEST")
    if identifier.version != 4 or type(generation) is not int or generation < 1:
        _error("DEMO_INVALID_REQUEST")
    return scope


def scope_for(ctx):
    if getattr(ctx, "app_mode", "demo") == "normal":
        if (
            ctx.scope != NORMAL_SCOPE
            or ctx.auth_epoch != 0
            or ctx.lease is not None
        ):
            raise ValueError("Invalid normal execution context")
        return NORMAL_SCOPE
    if getattr(ctx, "app_mode", "demo") != "demo":
        _error("DEMO_INVALID_REQUEST")
    scope = validate_scope(
        (getattr(ctx, "visitor_id", None), getattr(ctx, "generation", None))
    )
    if scope == NORMAL_SCOPE:
        _error("DEMO_INVALID_REQUEST")
    if (
        getattr(ctx, "lease", None) is None
        or type(getattr(ctx, "auth_epoch", None)) is not int
        or ctx.auth_epoch < 1
    ):
        _error("DEMO_INVALID_REQUEST")
    return scope


def _key(scope, kind, identifier):
    validate_scope(scope)
    if (
        kind not in KINDS
        or type(identifier) not in (int, str, UUID)
        or identifier == ""
    ):
        if scope == NORMAL_SCOPE:
            raise ValueError("Invalid execution key")
        _error("DEMO_INVALID_REQUEST")
    return scope, kind, identifier


class Lifecycle:
    def __init__(self, visitors: VisitorRegistry | None = None):
        self.visitors = visitors
        # #191 counts all leases; these references distinguish execution children.
        self._children: set[Lease] = set()
        self._tokens: dict[ExecutionKey, ExecutionToken] = {}
        self._legacy: dict[UUID, object] = {}
        self._normal_lock = asyncio.Lock()
        self._locks = WeakValueDictionary()

    def _authority(self):
        if self.visitors is None:
            raise RuntimeError("Demo lifecycle authority is not bound")
        return self.visitors

    @asynccontextmanager
    async def transition(self, ctx: DemoContext | NormalContext):
        if scope_for(ctx) == NORMAL_SCOPE:
            async with self._normal_lock:
                yield None
        else:
            async with self._authority().transition(
                ctx.verified_visitor
            ) as handle:
                handle.check(ctx)
                yield handle

    def register_normal(self, kind, identifier, task):
        key = _key(NORMAL_SCOPE, kind, identifier)
        if key in self._tokens:
            raise RuntimeError("Execution already registered")
        token = ExecutionToken(key, 0, task, None)
        self._tokens[key] = token
        return token

    async def register(
        self, ctx: DemoContext | NormalContext, kind, id, task: TaskControl
    ):
        scope = scope_for(ctx)
        key = _key(scope, kind, id)
        async with self.transition(ctx) as handle:
            if key in self._tokens:
                if scope == NORMAL_SCOPE:
                    raise RuntimeError("Execution already registered")
                _error("DEMO_BUSY", scope=scope, auth_epoch=ctx.auth_epoch)
            if handle is None:
                return self.register_normal(kind, id, task)
            lease = handle.retain_lease(ctx.lease)
            try:
                token = ExecutionToken(
                    key, ctx.auth_epoch, task, lease, ctx.verified_visitor
                )
                self._tokens[key] = token
                return token
            except BaseException:
                handle.release_lease(lease)
                raise

    def lookup(self, scope, kind, id):
        return self._tokens.get(_key(scope, kind, id))

    def tokens(self, scope, kind=None):
        validate_scope(scope)
        return tuple(
            t
            for t in self._tokens.values()
            if t.key[0] == scope and (kind is None or t.key[1] == kind)
        )

    async def pop(self, scope, kind, id, token):
        key = _key(scope, kind, id)
        if self._tokens.get(key) is not token:
            return False
        if not token.completed:
            if scope == NORMAL_SCOPE:
                raise RuntimeError("Execution is not complete")
            _error("DEMO_BUSY")
        if scope == NORMAL_SCOPE:
            async with self._normal_lock:
                return self._remove(key, token)
        async with self._authority().transition(
            token.verified_visitor
        ) as handle:
            if self._tokens.get(key) is not token:
                return False
            handle.release_lease(token.registry_lease)
            return self._remove(key, token)

    def _remove(self, key, token):
        if self._tokens.get(key) is not token:
            return False
        del self._tokens[key]
        self._legacy.pop(token.token_id, None)
        return True

    def _execution_children(self, scope):
        return any(
            not lease.released and (lease.visitor_id, lease.generation) == scope
            for lease in self._children
        )

    def busy_locked(self, handle: TransitionHandle, scope):
        validate_scope(scope)
        if (
            scope != NORMAL_SCOPE
            and handle.verified_visitor.visitor_id != scope[0]
        ):
            _error("INVALID_REQUEST")
        return bool(self.tokens(scope) or self._execution_children(scope))

    async def busy(self, scope, kind=None, id=None):
        validate_scope(scope)
        return any(
            id is None or token.key[2] == id
            for token in self.tokens(scope, kind)
        ) or self._execution_children(scope)

    def cancel(self, scope, kind, id):
        token = self.lookup(scope, kind, id)
        if token is None:
            return 0
        token.request_cancel()
        return 1

    def cancel_scope(self, scope):
        tokens = self.tokens(scope)
        for token in tokens:
            token.request_cancel()
        return len(tokens)

    async def drain(self, scope, timeout_seconds):
        validate_scope(scope)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise ValueError("Drain timeout must be finite and nonnegative")
        authority = None if scope == NORMAL_SCOPE else self._authority()
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    for token in self.tokens(scope):
                        if token.completed:
                            await self.pop(*token.key, token)
                    leases = authority.lease_count(scope) if authority else 0
                    remaining = max(len(self.tokens(scope)), leases)
                    if not remaining:
                        return dict(drained=True, remaining=0)
                    # Reap done tokens while #191 waits for request/child leases.
                    if leases:
                        await authority.wait_for_leases(scope, 0.01)
                    else:
                        await asyncio.sleep(0.01)
        except TimeoutError:
            remaining = max(
                len(self.tokens(scope)),
                authority.lease_count(scope) if authority else 0,
            )
            return dict(drained=remaining == 0, remaining=remaining)

    @asynccontextmanager
    async def resource_lock(self, scope, kind, id):
        key = _key(scope, kind, id)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        async with lock:
            yield

    @asynccontextmanager
    async def child_context(self, ctx):
        if scope_for(ctx) == NORMAL_SCOPE:
            yield ctx
            return
        async with self.transition(ctx) as handle:
            lease = handle.retain_lease(ctx.lease)
            self._children.add(lease)
        try:
            yield replace(ctx, lease=lease)
        finally:

            async def release():
                await self._authority().release_lease(lease)
                self._children.discard(lease)

            cancelled = False
            with anyio.CancelScope(shield=True):
                cleanup = asyncio.create_task(release())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
            if cancelled:
                raise asyncio.CancelledError

    async def assert_current(self, ctx):
        async with self.transition(ctx):
            pass

    @asynccontextmanager
    async def write_guard(self, ctx):
        if scope_for(ctx) == NORMAL_SCOPE:
            yield None
        else:
            async with self._authority().write_guard(ctx) as handle:
                yield handle

    def normal_view(self, kind, cancel):
        return NormalRegistry(self, kind, cancel)


class NormalRegistry(MutableMapping):
    # shortcut: legacy normal callers own completion; remove after #198/#200 migrate.
    def __init__(self, registry, kind, cancel):
        self.registry, self.kind, self.cancel = registry, kind, cancel

    def __getitem__(self, key):
        token = self.registry.lookup(NORMAL_SCOPE, self.kind, key)
        if token is None or token.token_id not in self.registry._legacy:
            raise KeyError(key)
        return self.registry._legacy[token.token_id]

    def __setitem__(self, key, value):
        if self.registry.lookup(NORMAL_SCOPE, self.kind, key) is not None:
            del self[key]
        token = self.registry.register_normal(
            self.kind,
            key,
            TaskControl(lambda: self.cancel(value), asyncio.Event()),
        )
        self.registry._legacy[token.token_id] = value

    def __delitem__(self, key):
        self[key]
        token = self.registry.lookup(NORMAL_SCOPE, self.kind, key)
        token.task_control.done.set()
        self.registry._remove(token.key, token)

    def __iter__(self):
        return iter(
            tuple(
                t.key[2]
                for t in self.registry.tokens(NORMAL_SCOPE, self.kind)
                if t.token_id in self.registry._legacy
            )
        )

    def __len__(self):
        return sum(1 for _ in self)


lifecycle = Lifecycle()
register = lifecycle.register
lookup = lifecycle.lookup
pop = lifecycle.pop
busy = lifecycle.busy
busy_locked = lifecycle.busy_locked
cancel = lifecycle.cancel
cancel_scope = lifecycle.cancel_scope
drain = lifecycle.drain
resource_lock = lifecycle.resource_lock
assert_current = lifecycle.assert_current
write_guard = lifecycle.write_guard

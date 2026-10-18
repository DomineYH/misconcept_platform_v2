"""D1-02 contract tests; visitor authority doubles never enter production."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.demo.lifecycle import Lifecycle, NormalContext, TaskControl


@dataclass(frozen=True)
class Lease:
    lease_id: object
    scope: tuple
    epoch: int
    execution: bool = False


@dataclass(frozen=True)
class Context:
    visitor_id: object
    generation: int
    auth_epoch: int
    lease: Lease
    factory: object
    app_mode: str = "demo"


class Visitors:
    """Own transition/currentness/lease counts, as #191 will at integration."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.generation = 1
        self.epoch = 1
        self.state = "ready"
        self.expired = False
        self.leases = {}
        self.visitor_id = uuid4()
        self.entered = False

    def context(self):
        lease = Lease(uuid4(), (self.visitor_id, 1), 1)
        self.leases[lease.lease_id] = lease
        return Context(self.visitor_id, 1, 1, lease, object())

    @asynccontextmanager
    async def transition(self, visitor):
        assert visitor == self.visitor_id
        async with self.lock:
            self.entered = True
            try:
                yield self
            finally:
                self.entered = False

    def check(self, ctx):
        assert self.entered
        checks = [
            (self.expired, "DEMO_EXPIRED", 410),
            (ctx.generation != self.generation, "DEMO_STALE_GENERATION", 409),
            (self.state == "resetting", "DEMO_RESET_IN_PROGRESS", 409),
            (self.state == "reset_failed", "DEMO_RESET_FAILED", 503),
            (ctx.auth_epoch != self.epoch, "DEMO_STALE_ROLE", 409),
            (
                ctx.lease is None or ctx.lease.lease_id not in self.leases,
                "DEMO_INVALID_REQUEST",
                422,
            ),
        ]
        for rejected, code, status in checks:
            if rejected:
                raise HTTPException(status, detail={"code": code})

    def retain_lease(self, lease):
        assert self.entered
        child = replace(lease, lease_id=uuid4(), execution=True)
        self.leases[child.lease_id] = child
        return child

    def release_lease(self, lease):
        assert self.entered
        self.leases.pop(lease.lease_id, None)

    def count(self, handle, scope, execution_only):
        assert handle is self and self.entered
        return sum(
            lease.scope == scope and (not execution_only or lease.execution)
            for lease in self.leases.values()
        )

    @asynccontextmanager
    async def write_guard(self, ctx):
        async with self.transition(ctx.visitor_id) as handle:
            handle.check(ctx)
            yield handle


async def test_registration_retains_execution_after_request_release():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, ctx.generation)
    cancelled, done = asyncio.Event(), asyncio.Event()
    token = await registry.register(
        ctx, "run", 7, TaskControl(cancelled.set, done)
    )
    assert token.key == (scope, "run", 7)
    assert token.registry_lease != ctx.lease
    assert token.captured_auth_epoch == 1
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(ctx.lease)
        assert registry.busy_locked(handle, scope)
    assert registry.lookup(scope, "run", 7) is token
    assert registry.cancel(scope, "run", 7) == 1
    assert cancelled.is_set() and not token.completed
    assert await registry.drain(scope, 0) == {"drained": False, "remaining": 1}
    done.set()
    assert await registry.drain(scope, 0) == {"drained": True, "remaining": 0}
    assert not visitors.leases


async def test_cancel_is_idempotent_without_completion():
    registry = Lifecycle()
    calls = []
    token = await registry.register(
        NormalContext(),
        "run",
        "id",
        TaskControl(lambda: calls.append("cancel"), asyncio.Event()),
    )
    assert registry.cancel_scope(("normal", 0)) == 1
    assert registry.cancel(("normal", 0), "run", "id") == 1
    assert calls == ["cancel"]
    assert not token.completed


@pytest.mark.parametrize(
    "kind", ["run", "analysis", "probe", "connection", "session", "comparison"]
)
@pytest.mark.parametrize("identifier", [7, str(uuid4()), uuid4()])
async def test_namespace_identity_and_retired_cleanup(kind, identifier):
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    other = (uuid4(), 1)
    next_scope = (ctx.visitor_id, 2)
    done = asyncio.Event()
    token = await registry.register(
        ctx, kind, identifier, TaskControl(lambda: None, done)
    )
    for unrelated in (other, next_scope, ("normal", 0)):
        assert registry.lookup(unrelated, kind, identifier) is None
        assert registry.cancel(unrelated, kind, identifier) == 0
        assert not await registry.pop(unrelated, kind, identifier, token)
    with pytest.raises(HTTPException) as error:
        await registry.pop(scope, kind, identifier, token)
    assert error.value.detail["code"] == "DEMO_BUSY"
    with pytest.raises(HTTPException):
        await registry.register(
            ctx, kind, identifier, TaskControl(lambda: None, asyncio.Event())
        )
    assert len(visitors.leases) == 2
    done.set()
    assert await registry.pop(scope, kind, identifier, token)
    replacement = await registry.register(
        ctx, kind, identifier, TaskControl(lambda: None, asyncio.Event())
    )
    assert not await registry.pop(scope, kind, identifier, token)
    assert registry.lookup(scope, kind, identifier) is replacement
    visitors.generation = 2
    replacement.task_control.done.set()
    assert await registry.pop(scope, kind, identifier, replacement)
    assert len(visitors.leases) == 1


@pytest.mark.parametrize(
    "scope", [None, (), ("", 1), ("normal", 1), (uuid4(), 0), (uuid4(), True)]
)
async def test_invalid_scope_is_rejected(scope):
    with pytest.raises(HTTPException) as error:
        Lifecycle().lookup(scope, "run", 7)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "DEMO_INVALID_REQUEST"


@pytest.mark.parametrize(
    "change,code",
    [
        ("generation", "DEMO_STALE_GENERATION"),
        ("epoch", "DEMO_STALE_ROLE"),
        ("expired", "DEMO_EXPIRED"),
        ("released", "DEMO_INVALID_REQUEST"),
        ("resetting", "DEMO_RESET_IN_PROGRESS"),
        ("reset_failed", "DEMO_RESET_FAILED"),
    ],
)
async def test_authority_errors_preserved_and_no_lease_retained(change, code):
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    if change in {"generation", "epoch"}:
        setattr(visitors, change, 2)
    elif change == "expired":
        visitors.expired = True
    elif change == "released":
        visitors.leases.clear()
    else:
        visitors.state = change
    before = len(visitors.leases)
    for operation in (
        registry.assert_current(ctx),
        registry.register(
            ctx, "analysis", "id", TaskControl(lambda: None, asyncio.Event())
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.detail["code"] == code
    assert len(visitors.leases) == before
    assert registry.lookup((ctx.visitor_id, 1), "analysis", "id") is None


async def test_register_rolls_back_retained_lease(monkeypatch):
    import src.demo.lifecycle as module

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()

    def fail(*args):
        raise RuntimeError("registration failed")

    monkeypatch.setattr(module, "ExecutionToken", fail)
    with pytest.raises(RuntimeError, match="registration failed"):
        await registry.register(
            ctx, "run", 7, TaskControl(lambda: None, asyncio.Event())
        )
    assert len(visitors.leases) == 1
    assert not registry.tokens((ctx.visitor_id, 1))


async def test_request_child_and_shield_leases_prevent_drain():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    async with visitors.transition(ctx.visitor_id) as handle:
        child = handle.retain_lease(ctx.lease)
        shield = handle.retain_lease(ctx.lease)
        handle.release_lease(ctx.lease)
    assert await registry.drain(scope, 0) == {"drained": False, "remaining": 2}
    assert await registry.busy(scope)
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(child)
        handle.release_lease(shield)
    assert await registry.drain(scope, 0) == {"drained": True, "remaining": 0}


async def test_resource_reservations_are_scoped_and_serialized():
    registry = Lifecycle()
    entered = asyncio.Event()
    scope = (uuid4(), 1)

    async def same_resource():
        async with registry.resource_lock(scope, "session", 7):
            entered.set()

    async with registry.resource_lock(scope, "session", 7):
        task = asyncio.create_task(same_resource())
        async with registry.resource_lock((uuid4(), 1), "session", 7):
            assert not entered.is_set()
        await asyncio.sleep(0)
        assert not entered.is_set()
    await task
    assert entered.is_set()


async def test_write_guard_serializes_commit_with_reset_and_role():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    acquired = asyncio.Event()
    changes = []

    async def mark():
        async with visitors.transition(ctx.visitor_id):
            changes.append("mark")
            visitors.generation += 1

    async with registry.write_guard(ctx):
        task = asyncio.create_task(mark())
        acquired.set()
        await asyncio.sleep(0)
        changes.append("commit")
    await task
    assert changes == ["commit", "mark"]
    with pytest.raises(HTTPException) as error:
        async with registry.write_guard(ctx):
            changes.append("late commit")
    assert error.value.detail["code"] == "DEMO_STALE_GENERATION"
    assert changes == ["commit", "mark"]
    assert ctx.factory is ctx.factory and ctx.generation == 1


async def test_register_and_role_change_use_same_transition():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    token = await registry.register(
        ctx, "run", 7, TaskControl(lambda: None, asyncio.Event())
    )
    async with visitors.transition(ctx.visitor_id) as handle:
        assert registry.busy_locked(handle, scope)
        handle.check(ctx)  # current same role remains a no-op
    token.task_control.done.set()
    await registry.pop(scope, "run", 7, token)
    async with visitors.transition(ctx.visitor_id) as handle:
        assert not registry.busy_locked(handle, scope)
        visitors.epoch += 1
    with pytest.raises(HTTPException) as error:
        await registry.register(
            ctx, "run", 7, TaskControl(lambda: None, asyncio.Event())
        )
    assert error.value.detail["code"] == "DEMO_STALE_ROLE"
    assert len(visitors.leases) == 1


async def test_normal_never_calls_demo_authority():
    registry = Lifecycle(object())
    ctx = NormalContext(factory=object())
    await registry.assert_current(ctx)
    async with registry.write_guard(ctx) as handle:
        assert handle is None
    token = await registry.register(
        ctx, "probe", 1, TaskControl(lambda: None, asyncio.Event())
    )
    assert token.registry_lease is None
    assert await registry.busy(("normal", 0))
    token.task_control.done.set()
    assert await registry.pop(("normal", 0), "probe", 1, token)


async def test_legacy_view_is_normal_only_and_shares_token_registry():
    registry = Lifecycle()
    signals = registry.normal_view("run", lambda event: event.set())
    signal = signals["id"] = asyncio.Event()
    token = registry.lookup(("normal", 0), "run", "id")
    assert token is not None
    assert registry.cancel(("normal", 0), "run", "id") == 1
    assert signal.is_set() and not token.completed
    assert registry.lookup((uuid4(), 1), "run", "id") is None
    assert signals.pop("id") is signal
    assert not registry.tokens(("normal", 0)) and not signals


@pytest.mark.parametrize(
    "kind", ["run", "analysis", "probe", "connection", "session", "comparison"]
)
@pytest.mark.parametrize("identifier", [7, str(uuid4()), uuid4()])
async def test_reset_cancel_does_not_touch_same_id_in_other_visit_or_generation(
    kind, identifier
):
    first, second = Visitors(), Visitors()

    class Authority:
        def transition(self, visitor):
            return {first.visitor_id: first, second.visitor_id: second}[
                visitor
            ].transition(visitor)

        def write_guard(self, ctx):
            return {first.visitor_id: first, second.visitor_id: second}[
                ctx.visitor_id
            ].write_guard(ctx)

    registry = Lifecycle(
        Authority(),
        lease_count=lambda handle, scope, execution_only: handle.count(
            handle, scope, execution_only
        ),
    )
    a, b = first.context(), second.context()
    cancelled_a, cancelled_b, cancelled_next = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    token_a = await registry.register(
        a, kind, identifier, TaskControl(cancelled_a.set, asyncio.Event())
    )
    token_b = await registry.register(
        b, kind, identifier, TaskControl(cancelled_b.set, asyncio.Event())
    )
    first.generation = first.epoch = 2
    new_lease = Lease(uuid4(), (first.visitor_id, 2), 2)
    first.leases[new_lease.lease_id] = new_lease
    next_ctx = replace(a, generation=2, auth_epoch=2, lease=new_lease)
    token_next = await registry.register(
        next_ctx,
        kind,
        identifier,
        TaskControl(cancelled_next.set, asyncio.Event()),
    )
    assert registry.cancel_scope((first.visitor_id, 1)) == 1
    assert (
        cancelled_a.is_set()
        and not cancelled_b.is_set()
        and not cancelled_next.is_set()
    )
    token_a.task_control.done.set()
    assert await registry.pop(*token_a.key, token_a)
    assert registry.lookup((second.visitor_id, 1), kind, identifier) is token_b
    assert (
        registry.lookup((first.visitor_id, 2), kind, identifier) is token_next
    )
    for token in (token_b, token_next):
        token.task_control.done.set()
        await registry.pop(*token.key, token)


async def test_concurrent_registration_has_one_token_and_one_child_lease():
    visitors = Visitors()
    ctx = visitors.context()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    results = await asyncio.gather(
        *(
            registry.register(
                ctx,
                "analysis",
                "id",
                TaskControl(lambda: None, asyncio.Event()),
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, HTTPException) for result in results) == 1
    assert len(registry.tokens((ctx.visitor_id, 1))) == 1
    assert len(visitors.leases) == 2


async def test_reset_handoff_releases_own_request_but_not_other_live_request():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    other = visitors.context()
    scope = (ctx.visitor_id, 1)
    async with visitors.transition(ctx.visitor_id) as handle:
        visitors.generation += 1
        visitors.state = "resetting"
    # #194 closes/rolls back its DB before this authority release.
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(ctx.lease)
    assert registry.cancel_scope(scope) == 0
    assert await registry.drain(scope, 0) == {"drained": False, "remaining": 1}
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(other.lease)
    assert await registry.drain(scope, 0) == {"drained": True, "remaining": 0}


async def test_drain_waits_without_holding_transition():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    draining = asyncio.create_task(registry.drain(scope, 1))
    await asyncio.sleep(0)
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(ctx.lease)
    assert await asyncio.wait_for(draining, 1) == {
        "drained": True,
        "remaining": 0,
    }


async def test_child_context_changes_only_lease_and_releases_after_retirement():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    async with registry.child_context(ctx) as child:
        assert child.lease != ctx.lease
        assert replace(child, lease=ctx.lease) == ctx
        assert child.factory is ctx.factory
        visitors.generation = 2
    assert len(visitors.leases) == 1


async def test_drain_timeout_includes_waiting_for_transition():
    visitors = Visitors()
    ctx = visitors.context()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    async with visitors.transition(ctx.visitor_id):
        result = await asyncio.wait_for(
            registry.drain((ctx.visitor_id, 1), 0.01), 1
        )
    assert result == {"drained": False, "remaining": 1}
    assert len(visitors.leases) == 1

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


async def test_write_guard_serializes_commit_with_reset():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    original_factory = ctx.factory
    changes = []

    async def mark():
        async with visitors.transition(ctx.visitor_id):
            changes.append("mark")
            visitors.generation += 1

    async with registry.write_guard(ctx):
        task = asyncio.create_task(mark())
        await asyncio.sleep(0)
        changes.append("commit")
    await task
    assert changes == ["commit", "mark"]
    with pytest.raises(HTTPException) as error:
        async with registry.write_guard(ctx):
            changes.append("late commit")
    assert error.value.detail["code"] == "DEMO_STALE_GENERATION"
    assert changes == ["commit", "mark"]
    assert ctx.factory is original_factory and ctx.generation == 1


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


@pytest.mark.parametrize("kind", ["analysis", "probe"])
async def test_prestart_cancel_waits_for_callback_cleanup(monkeypatch, kind):
    from src.services import analysis_runs, probe_execution, probe_lifecycle

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event()
    writes = []

    async def cleanup(*args, ctx, registry):
        cleanup_started.set()
        await cleanup_release.wait()
        async with registry.write_guard(ctx):
            writes.append("cleanup")

    if kind == "analysis":
        monkeypatch.setattr(analysis_runs, "finish_failure", cleanup)
        token = await analysis_runs.start_scoped_analysis(
            ctx, {"run_id": "id"}, registry=registry
        )
    else:
        from types import SimpleNamespace

        monkeypatch.setattr(probe_lifecycle, "cancel_reserved_probe", cleanup)
        permit = SimpleNamespace(task=None, release=lambda: None)
        token = await probe_execution.start_scoped_probe(
            ctx, 7, permit, registry=registry
        )
    token.request_cancel()  # before the task ever executes its body
    await asyncio.wait_for(cleanup_started.wait(), 2)
    async with visitors.transition(ctx.visitor_id) as handle:
        handle.release_lease(ctx.lease)
    assert not token.completed
    assert await registry.drain(scope, 0) == {"drained": False, "remaining": 1}
    cleanup_release.set()
    await asyncio.wait_for(token.wait_done(), 2)
    assert await registry.drain(scope, 0.1) == {"drained": True, "remaining": 0}
    assert writes == ["cleanup"]


@pytest.mark.parametrize("execute", [False, True])
async def test_demo_cleanup_cli_rejects_before_database_open(
    monkeypatch, capsys, tmp_path, execute
):
    from src.cli import cleanup_comparisons as cli

    def forbidden(*args, **kwargs):
        pytest.fail("Demo CLI opened an engine")

    monkeypatch.setattr(cli, "create_async_engine", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "cleanup_comparisons",
            "--database",
            str(tmp_path / "never.db"),
            "--execute" if execute else "--dry-run",
        ],
    )
    assert cli.main(app_mode="demo") == 1
    assert "DEMO_ACTION_FORBIDDEN" in capsys.readouterr().out
    with pytest.raises(ValueError, match="DEMO_ACTION_FORBIDDEN"):
        await cli.run(tmp_path / "never.db", execute, app_mode="demo")
    assert not (tmp_path / "never.db").exists()


@pytest.mark.parametrize("mode", ["", "invalid"])
def test_cleanup_cli_invalid_mode_fails_closed(mode, capsys):
    from src.cli.cleanup_comparisons import main

    assert main(app_mode=mode) == 1
    assert "DEMO_CONFIGURATION_INVALID" in capsys.readouterr().out


@pytest.mark.parametrize(
    "operation",
    [
        "analysis_failure",
        "probe_success",
        "probe_cancel",
        "orphan",
        "probe_orphan",
    ],
)
async def test_late_owned_callbacks_never_open_factory(operation):
    from src.services import (
        analysis_runs,
        generation_lifecycle,
        probe_lifecycle,
    )

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    visitors.generation = 2

    def forbidden():
        pytest.fail("Late callback opened discarded factory")

    callbacks = {
        "analysis_failure": lambda: analysis_runs.finish_failure(
            forbidden, "id", "failed", "interrupted", ctx=ctx, registry=registry
        ),
        "probe_success": lambda: probe_lifecycle.finish_probe(
            forbidden, 7, "succeeded", ctx=ctx, registry=registry
        ),
        "probe_cancel": lambda: probe_lifecycle.cancel_reserved_probe(
            forbidden, 7, ctx=ctx, registry=registry
        ),
        "orphan": lambda: generation_lifecycle.interrupt_orphans(
            forbidden, ctx=ctx, registry=registry
        ),
        "probe_orphan": lambda: probe_lifecycle.interrupt_probe_orphans(
            forbidden, ctx=ctx, registry=registry
        ),
    }
    with pytest.raises(HTTPException) as error:
        await callbacks[operation]()
    assert error.value.detail["code"] == "DEMO_STALE_GENERATION"


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


async def test_rejected_probe_registration_releases_admitted_permit():
    from types import SimpleNamespace

    from src.services.probe_execution import start_scoped_probe

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    visitors.epoch = 2
    released = []
    permit = SimpleNamespace(release=lambda: released.append(True))
    with pytest.raises(HTTPException) as error:
        await start_scoped_probe(ctx, 7, permit, registry=registry)
    assert error.value.detail["code"] == "DEMO_STALE_ROLE"
    assert released == [True]
    assert len(visitors.leases) == 1


async def test_analysis_reservation_keeps_execution_lease_until_task_handoff(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.services import analysis_runs

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    events = []

    async def reserve(*args, ctx, **kwargs):
        async with registry.write_guard(ctx) as handle:
            assert registry.busy_locked(handle, scope)
        events.append("reservation committed")
        return {"latest_run": {"status": "running"}}, {"run_id": "id"}

    async def start(child, execution, **kwargs):
        async with registry.transition(child) as handle:
            assert registry.busy_locked(handle, scope)
            assert child.factory is ctx.factory
        events.append("task registered")

    monkeypatch.setattr(analysis_runs, "reserve_analysis", reserve)
    monkeypatch.setattr(analysis_runs, "start_scoped_analysis", start)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    response = await analysis_runs.request_analysis(
        db, 7, 1, "request", ctx=ctx, registry=registry
    )
    assert response.status_code == 202
    assert events == ["reservation committed", "task registered"]
    assert len(visitors.leases) == 1


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


@pytest.mark.parametrize("reset_first", [False, True])
async def test_cancelled_analysis_launch_cleans_reservation_under_retained_child(
    monkeypatch, reset_first
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.services import analysis_runs

    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    waiting = asyncio.Event()
    writes = []

    async def reserve(*args, **kwargs):
        return {"latest_run": {"status": "running"}}, {"run_id": "id"}

    async def start(*args, **kwargs):
        waiting.set()
        await asyncio.Event().wait()

    async def failure(factory, run_id, status, code, *, ctx, registry):
        async with registry.write_guard(ctx) as handle:
            assert registry.busy_locked(handle, (ctx.visitor_id, 1))
            writes.append(status)

    monkeypatch.setattr(analysis_runs, "reserve_analysis", reserve)
    monkeypatch.setattr(analysis_runs, "start_scoped_analysis", start)
    monkeypatch.setattr(analysis_runs, "finish_failure", failure)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    task = asyncio.create_task(
        analysis_runs.request_analysis(
            db, 7, 1, "request", ctx=ctx, registry=registry
        )
    )
    await asyncio.wait_for(waiting.wait(), 2)
    if reset_first:
        visitors.generation = 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert writes == ([] if reset_first else ["interrupted"])
    assert len(visitors.leases) == 1


async def test_demo_cli_process_rejects_without_importing_application_database():
    import os
    import sys

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; from src.cli.cleanup_comparisons import main; result = main(); "
        "assert result == 1; assert 'src.db.connection' not in sys.modules; "
        "assert 'src.config' not in sys.modules",
        env={**os.environ, "APP_MODE": "demo"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    assert b"DEMO_ACTION_FORBIDDEN" in stdout


async def test_child_release_finishes_under_repeated_cancellation_and_contended_transition():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    entered = asyncio.Event()

    async def execution():
        async with registry.child_context(ctx):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(execution())
    await asyncio.wait_for(entered.wait(), 2)
    async with visitors.transition(ctx.visitor_id):
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert len(visitors.leases) == 2
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert len(visitors.leases) == 1


@pytest.mark.parametrize("kind", ["analysis", "probe"])
async def test_shutdown_consumes_finished_cleanup_without_waiting_for_queued_done_callback(
    monkeypatch, kind
):
    from src.services import analysis_runs, probe_execution

    module = analysis_runs if kind == "analysis" else probe_execution

    async def finished():
        return None

    cleanup = asyncio.create_task(finished())
    await cleanup
    monkeypatch.setattr(module, "cleanup_tasks", {cleanup})
    monkeypatch.setattr(
        module, "active_analyses" if kind == "analysis" else "active_probes", {}
    )
    stop = module.stop_analyses if kind == "analysis" else module.stop_probes
    await stop()
    assert not module.cleanup_tasks


@pytest.mark.parametrize("missing", ["context", "namespace", "lease", "epoch"])
async def test_missing_demo_context_fields_are_invalid_requests(missing):
    from types import SimpleNamespace

    fields = dict(
        app_mode="demo",
        visitor_id=uuid4(),
        generation=1,
        auth_epoch=1,
        lease=object(),
    )
    if missing == "namespace":
        fields.pop("visitor_id")
    elif missing in {"lease", "epoch"}:
        fields.pop("lease" if missing == "lease" else "auth_epoch")
    ctx = None if missing == "context" else SimpleNamespace(**fields)
    with pytest.raises(HTTPException) as error:
        await Lifecycle().register(
            ctx, "run", "id", TaskControl(lambda: None, asyncio.Event())
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "DEMO_INVALID_REQUEST"


@pytest.mark.parametrize(
    "visitor_id,generation",
    [
        ("normal", 0),
        ("normal", 1),
        ("invalid", 1),
        ("00000000-0000-1000-8000-000000000001", 1),
        (uuid4(), 0),
        (uuid4(), -1),
        (uuid4(), True),
    ],
)
@pytest.mark.parametrize(
    "operation",
    ["register", "assert_current", "write_guard", "child_context", "cancel"],
)
async def test_invalid_demo_namespace_never_enters_normal_scope(
    visitor_id, generation, operation
):
    from types import SimpleNamespace

    from src.demo.lifecycle import NORMAL_SCOPE, scope_for

    registry = Lifecycle()
    cancelled = asyncio.Event()
    normal = await registry.register(
        NormalContext(),
        "run",
        "existing",
        TaskControl(cancelled.set, asyncio.Event()),
    )
    ctx = SimpleNamespace(
        app_mode="demo",
        visitor_id=visitor_id,
        generation=generation,
        auth_epoch=1,
        lease=object(),
        factory=object(),
    )
    with pytest.raises(HTTPException) as error:
        if operation == "register":
            await registry.register(
                ctx, "run", "demo", TaskControl(lambda: None, asyncio.Event())
            )
        elif operation == "assert_current":
            await registry.assert_current(ctx)
        elif operation == "write_guard":
            async with registry.write_guard(ctx):
                pytest.fail("Invalid demo context entered write guard")
        elif operation == "child_context":
            async with registry.child_context(ctx):
                pytest.fail("Invalid demo context retained a child context")
        else:
            registry.cancel(scope_for(ctx), "run", "existing")
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "DEMO_INVALID_REQUEST"
    assert registry.tokens(NORMAL_SCOPE) == (normal,)
    assert not cancelled.is_set() and not normal.completed
    normal.task_control.done.set()
    assert await registry.pop(*normal.key, normal)


async def test_role_change_first_rejects_old_write_guard_without_writes():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    writes = []
    async with visitors.transition(ctx.visitor_id):
        visitors.epoch += 1
    with pytest.raises(HTTPException) as error:
        async with registry.write_guard(ctx):
            writes.append("commit")
    assert error.value.detail["code"] == "DEMO_STALE_ROLE"
    assert error.value.status_code == 409
    assert writes == []


async def test_write_guard_first_commits_before_role_change():
    visitors = Visitors()
    registry = Lifecycle(visitors, lease_count=visitors.count)
    ctx = visitors.context()
    attempting_role_change = asyncio.Event()
    changes = []

    async def change_role():
        attempting_role_change.set()
        async with visitors.transition(ctx.visitor_id):
            visitors.epoch += 1
            changes.append("role changed")

    async with registry.write_guard(ctx):
        task = asyncio.create_task(change_role())
        await asyncio.wait_for(attempting_role_change.wait(), 2)
        assert changes == [] and visitors.epoch == ctx.auth_epoch
        changes.append("commit")
    await asyncio.wait_for(task, 2)
    assert changes == ["commit", "role changed"]
    assert visitors.epoch == ctx.auth_epoch + 1

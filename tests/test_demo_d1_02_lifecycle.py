"""D1-02 contract tests; visitor authority doubles never enter production."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from src.demo.lifecycle import Lifecycle, NormalContext, TaskControl
from src.demo.visitors import DemoError


@dataclass(frozen=True, eq=False)
class Lease:
    lease_id: object
    scope: tuple
    epoch: int
    released: bool = False

    @property
    def visitor_id(self):
        return self.scope[0]

    @property
    def generation(self):
        return self.scope[1]


@dataclass(frozen=True)
class Context:
    visitor_id: object
    generation: int
    auth_epoch: int
    lease: Lease
    factory: object
    verified_visitor: object
    app_mode: str = "demo"


class DoubleHandle:
    def __init__(self, visitors):
        self.visitors = visitors

    @property
    def verified_visitor(self):
        assert self.visitors.entered
        return self.visitors.verified_visitor

    def check(self, ctx):
        return self.visitors.check(ctx)

    def retain_lease(self, lease):
        assert self.visitors.entered
        child = replace(lease, lease_id=uuid4())
        self.visitors.leases[child.lease_id] = child
        return child

    def release_lease(self, lease):
        assert self.visitors.entered
        if lease.released:
            return False
        object.__setattr__(lease, "released", True)
        self.visitors.leases.pop(lease.lease_id, None)
        return True


class Visitors:
    """Small capability/lease double for fault injection; real matrix below."""

    def __init__(self):
        from types import SimpleNamespace

        self.lock = asyncio.Lock()
        self.generation = 1
        self.epoch = 1
        self.state = "ready"
        self.expired = False
        self.leases = {}
        self.visitor_id = uuid4()
        self.verified_visitor = SimpleNamespace(visitor_id=self.visitor_id)
        self.entered = False

    def context(self):
        lease = Lease(uuid4(), (self.visitor_id, 1), 1)
        self.leases[lease.lease_id] = lease
        return Context(
            self.visitor_id, 1, 1, lease, object(), self.verified_visitor
        )

    @asynccontextmanager
    async def transition(self, visitor):
        assert visitor is self.verified_visitor
        async with self.lock:
            self.entered = True
            try:
                yield DoubleHandle(self)
            finally:
                self.entered = False

    def check(self, ctx):
        assert self.entered and ctx.verified_visitor is self.verified_visitor
        checks = [
            (self.expired, "DEMO_EXPIRED"),
            (ctx.generation != self.generation, "DEMO_STALE_GENERATION"),
            (self.state == "resetting", "DEMO_RESET_IN_PROGRESS"),
            (self.state == "reset_failed", "DEMO_RESET_FAILED"),
            (ctx.auth_epoch != self.epoch, "DEMO_STALE_ROLE"),
            (
                ctx.lease is None or ctx.lease.lease_id not in self.leases,
                "DEMO_INVALID_REQUEST",
            ),
        ]
        for rejected, code in checks:
            if rejected:
                raise DemoError(code)

    def lease_count(self, scope):
        return sum(
            lease.scope == scope and not lease.released
            for lease in self.leases.values()
        )

    async def wait_for_leases(self, scope, timeout_seconds):
        try:
            async with asyncio.timeout(timeout_seconds):
                while self.lease_count(scope):
                    await asyncio.sleep(0.001)
            return True
        except TimeoutError:
            return False

    async def release_lease(self, lease):
        async with self.transition(self.verified_visitor) as handle:
            return handle.release_lease(lease)

    @asynccontextmanager
    async def write_guard(self, ctx):
        async with self.transition(ctx.verified_visitor) as handle:
            handle.check(ctx)
            yield handle


async def test_registration_retains_execution_after_request_release():
    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    scope = (ctx.visitor_id, ctx.generation)
    cancelled, done = asyncio.Event(), asyncio.Event()
    token = await registry.register(
        ctx, "run", 7, TaskControl(cancelled.set, done)
    )
    assert token.key == (scope, "run", 7)
    assert token.registry_lease != ctx.lease
    assert token.captured_auth_epoch == 1
    async with visitors.transition(ctx.verified_visitor) as handle:
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
    "scope", [None, (), ("", 1), ("normal", 1), (uuid4(), 0), (uuid4(), True)]
)
async def test_invalid_scope_is_rejected(scope):
    with pytest.raises(DemoError) as error:
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
    registry = Lifecycle(visitors)
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
        with pytest.raises(DemoError) as error:
            await operation
        assert error.value.detail["code"] == code
    assert len(visitors.leases) == before
    assert registry.lookup((ctx.visitor_id, 1), "analysis", "id") is None


async def test_register_rolls_back_retained_lease(monkeypatch):
    import src.demo.lifecycle as module

    visitors = Visitors()
    registry = Lifecycle(visitors)
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
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    async with registry.child_context(ctx), registry.child_context(ctx):
        await visitors.release_lease(ctx.lease)
        assert await registry.drain(scope, 0) == {
            "drained": False,
            "remaining": 2,
        }
        assert await registry.busy(scope)
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
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    original_factory = ctx.factory
    changes = []

    async def mark():
        async with visitors.transition(ctx.verified_visitor):
            changes.append("mark")
            visitors.generation += 1

    async with registry.write_guard(ctx):
        task = asyncio.create_task(mark())
        await asyncio.sleep(0)
        changes.append("commit")
    await task
    assert changes == ["commit", "mark"]
    with pytest.raises(DemoError) as error:
        async with registry.write_guard(ctx):
            changes.append("late commit")
    assert error.value.detail["code"] == "DEMO_STALE_GENERATION"
    assert changes == ["commit", "mark"]
    assert ctx.factory is original_factory and ctx.generation == 1


async def test_register_and_role_change_use_same_transition():
    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    token = await registry.register(
        ctx, "run", 7, TaskControl(lambda: None, asyncio.Event())
    )
    async with visitors.transition(ctx.verified_visitor) as handle:
        assert registry.busy_locked(handle, scope)
        handle.check(ctx)  # current same role remains a no-op
    token.task_control.done.set()
    await registry.pop(scope, "run", 7, token)
    async with visitors.transition(ctx.verified_visitor) as handle:
        assert not registry.busy_locked(handle, scope)
        visitors.epoch += 1
    with pytest.raises(DemoError) as error:
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
    registry = Lifecycle(visitors)
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
    async with visitors.transition(ctx.verified_visitor) as handle:
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
    registry = Lifecycle(visitors)
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
    with pytest.raises(DemoError) as error:
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
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    visitors.epoch = 2
    released = []
    permit = SimpleNamespace(release=lambda: released.append(True))
    with pytest.raises(DemoError) as error:
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
    registry = Lifecycle(visitors)
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
                visitor.visitor_id
            ].transition(visitor)

        def write_guard(self, ctx):
            return {first.visitor_id: first, second.visitor_id: second}[
                ctx.visitor_id
            ].write_guard(ctx)

        def lease_count(self, scope):
            return {first.visitor_id: first, second.visitor_id: second}[
                scope[0]
            ].lease_count(scope)

        async def wait_for_leases(self, scope, timeout_seconds):
            return await {first.visitor_id: first, second.visitor_id: second}[
                scope[0]
            ].wait_for_leases(scope, timeout_seconds)

        async def release_lease(self, lease):
            return await {first.visitor_id: first, second.visitor_id: second}[
                lease.visitor_id
            ].release_lease(lease)

    registry = Lifecycle(Authority())
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
    registry = Lifecycle(visitors)
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
    assert sum(isinstance(result, DemoError) for result in results) == 1
    assert len(registry.tokens((ctx.visitor_id, 1))) == 1
    assert len(visitors.leases) == 2


async def test_reset_handoff_releases_own_request_but_not_other_live_request():
    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    other = visitors.context()
    scope = (ctx.visitor_id, 1)
    async with visitors.transition(ctx.verified_visitor) as handle:
        visitors.generation += 1
        visitors.state = "resetting"
    # Lease-only evidence; #193/#194 own request-DB handoff/cleanup ordering.
    async with visitors.transition(ctx.verified_visitor) as handle:
        handle.release_lease(ctx.lease)
    assert registry.cancel_scope(scope) == 0
    assert await registry.drain(scope, 0) == {"drained": False, "remaining": 1}
    async with visitors.transition(ctx.verified_visitor) as handle:
        handle.release_lease(other.lease)
    assert await registry.drain(scope, 0) == {"drained": True, "remaining": 0}


async def test_drain_waits_without_holding_transition():
    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    scope = (ctx.visitor_id, 1)
    draining = asyncio.create_task(registry.drain(scope, 1))
    await asyncio.sleep(0)
    async with visitors.transition(ctx.verified_visitor) as handle:
        handle.release_lease(ctx.lease)
    assert await asyncio.wait_for(draining, 1) == {
        "drained": True,
        "remaining": 0,
    }


async def test_child_context_changes_only_lease_and_releases_after_retirement():
    visitors = Visitors()
    registry = Lifecycle(visitors)
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
    registry = Lifecycle(visitors)
    scope = (ctx.visitor_id, ctx.generation)
    token = await registry.register(
        ctx, "analysis", "id", TaskControl(lambda: None, asyncio.Event())
    )
    await visitors.release_lease(ctx.lease)
    token.task_control.done.set()
    async with visitors.transition(ctx.verified_visitor):
        result = await asyncio.wait_for(
            asyncio.create_task(registry.drain(scope, 0.01)), 1
        )
        assert result == {"drained": False, "remaining": 1}
        assert registry.lookup(*token.key) is token
        assert not token.registry_lease.released
        assert visitors.lease_count(scope) == 1
    assert await registry.drain(scope, 1) == {"drained": True, "remaining": 0}
    assert registry.lookup(*token.key) is None
    assert token.registry_lease.released
    assert visitors.lease_count(scope) == 0


@pytest.mark.parametrize("reset_first", [False, True])
async def test_cancelled_analysis_launch_cleans_reservation_under_retained_child(
    monkeypatch, reset_first
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.services import analysis_runs

    visitors = Visitors()
    registry = Lifecycle(visitors)
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
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    entered = asyncio.Event()

    async def execution():
        async with registry.child_context(ctx):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(execution())
    await asyncio.wait_for(entered.wait(), 2)
    async with visitors.transition(ctx.verified_visitor):
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
    with pytest.raises(DemoError) as error:
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
    with pytest.raises(DemoError) as error:
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
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    writes = []
    async with visitors.transition(ctx.verified_visitor):
        visitors.epoch += 1
    with pytest.raises(DemoError) as error:
        async with registry.write_guard(ctx):
            writes.append("commit")
    assert error.value.detail["code"] == "DEMO_STALE_ROLE"
    assert error.value.status_code == 409
    assert writes == []


async def test_write_guard_first_commits_before_role_change():
    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = visitors.context()
    attempting_role_change = asyncio.Event()
    changes = []

    async def change_role():
        attempting_role_change.set()
        async with visitors.transition(ctx.verified_visitor):
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


async def make_lifecycle_root(tmp_path, *, max_db_bytes=None):
    """A tiny synthetic seed, using the official explicit-engine installer."""
    import hashlib
    import json
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.db.migrations.migrate import run_all_migrations
    from src.demo.database import DatabaseRoot, SeedSpec

    root = DatabaseRoot(
        tmp_path / "demo", initialize=True, max_db_bytes=max_db_bytes
    ).open()
    seed = root.path / "seed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{seed}")
    await run_all_migrations(db_engine=engine)
    await engine.dispose()
    manifest = dict(
        manifest_version=1,
        contract_version="d0-03-v1-r3",
        content_version="d0-02-v1",
        fixture_version="demo-fixture-v1",
        db_schema_version="unit_demo_metadata.sql",
        migration_set_sha256="a" * 64,
        scenario_config_schema_version=1,
        analysis_schema_version=2,
        provider="demo",
        model_id="demo-classroom-v1",
        capability_definition_version="demo-cap-v1",
        role_contract_versions=dict(
            student="s1-v1", mentor="s3-v1", analysis="s4-v2"
        ),
        scenario_ref="unit-fraction-v1",
        supported_config_sha256="b" * 64,
        seed_version="demo-seed-v1",
        seed_timestamp="2026-10-18T00:00:00Z",
        seed_refs=dict(
            admin=1,
            teacher=2,
            group=1,
            scenario=1,
            completed_session=1,
            model=1,
            connection=1,
        ),
        files=[],
        lookup_version=1,
        responses={},
        seed_provenance=dict(
            source="synthetic",
            generator="src/demo/seed.py",
            content_version="d0-02-v1",
        ),
    )
    manifest_path = root.path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with sqlite3.connect(seed) as db:
        db.executemany(
            "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
            [
                (
                    1,
                    "fictional-admin",
                    "Fictional admin",
                    "!login-disabled",
                    "admin",
                    "2026-10-18 00:00:00",
                ),
                (
                    2,
                    "fictional-teacher",
                    "Fictional teacher",
                    "!login-disabled",
                    "teacher",
                    "2026-10-18 00:00:00",
                ),
            ],
        )
        # G2 owns production metadata DDL; this models that explicit boundary only.
        db.execute(
            "CREATE TABLE demo_metadata (id INTEGER PRIMARY KEY, kind TEXT, contract_version TEXT, content_version TEXT, fixture_version TEXT, db_schema_version TEXT, manifest_sha256 TEXT, visitor_id TEXT, generation INTEGER)"
        )
        db.execute(
            "INSERT INTO demo_metadata VALUES (1,'demo_seed','d0-03-v1-r3','d0-02-v1','demo-fixture-v1','unit_demo_metadata.sql',?,NULL,NULL)",
            (digest,),
        )
        db.execute(
            "INSERT INTO _migrations(filename) VALUES ('unit_demo_metadata.sql')"
        )
        db.execute(
            "CREATE TABLE synthetic_record(id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        db.execute("INSERT INTO synthetic_record VALUES(1,'fictional')")
        history = tuple(
            row[0]
            for row in db.execute(
                "SELECT filename FROM _migrations ORDER BY id"
            )
        )

    def verify_content(db, manifest):
        if (
            db.execute(
                "SELECT body FROM synthetic_record WHERE id=1"
            ).fetchone()[0]
            != "fictional"
        ):
            raise DemoError("VERSION_MISMATCH")

    root.validate_seed(
        SeedSpec(
            seed,
            manifest_path,
            "unit_demo_metadata.sql",
            "a" * 64,
            history,
            verify_content,
        )
    )
    return root


@pytest.fixture
async def real_lifecycle(tmp_path):
    import secrets
    from types import SimpleNamespace

    from src.demo.visitors import VisitorRegistry

    root = await make_lifecycle_root(tmp_path)
    lifecycle = Lifecycle()
    visitors = VisitorRegistry(root, lifecycle=lifecycle)
    lifecycle.visitors = visitors
    contexts = []

    async def context(role="admin"):
        ready = await visitors.create_visit(
            visitors.verify_bootstrap(secrets.token_hex(16)), str(uuid4()), role
        )
        lease = await visitors.acquire_lease(
            ready.snapshot.ref, ready.snapshot.auth_epoch
        )
        ctx = await visitors.make_context(
            ready.verified_visitor, lease, user_id=1 if role == "admin" else 2
        )
        contexts.append(ctx)
        return ctx

    try:
        yield SimpleNamespace(
            lifecycle=lifecycle, visitors=visitors, root=root, context=context
        )
    finally:
        for ctx in contexts:
            await visitors.release_lease(ctx.lease)
        for scope in {ctx.scope for ctx in contexts}:
            for token in lifecycle.tokens(scope):
                token.task_control.done.set()
            await lifecycle.drain(scope, 1)
        await visitors.shutdown()
        root.close()


async def test_real_visitor_capability_registration_and_request_release(
    real_lifecycle,
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    await fixture.lifecycle.assert_current(ctx)
    token = await fixture.lifecycle.register(
        ctx, "run", "id", TaskControl(lambda: None, asyncio.Event())
    )
    assert token.verified_visitor is ctx.verified_visitor
    assert token.registry_lease is not ctx.lease
    assert fixture.visitors.lease_count(ctx.scope) == 2
    await fixture.visitors.release_lease(ctx.lease)
    assert fixture.visitors.lease_count(ctx.scope) == 1
    token.task_control.done.set()
    assert await fixture.lifecycle.pop(*token.key, token)
    assert fixture.visitors.lease_count(ctx.scope) == 0


@pytest.mark.parametrize(
    "kind", ["run", "analysis", "probe", "connection", "session", "comparison"]
)
@pytest.mark.parametrize("identifier", [7, str(uuid4()), uuid4()])
async def test_real_namespaces_isolate_equal_ids_and_allow_retired_pop(
    real_lifecycle, kind, identifier
):
    fixture = real_lifecycle
    first, second = await fixture.context(), await fixture.context()
    cancelled_a, cancelled_b = asyncio.Event(), asyncio.Event()
    token_a = await fixture.lifecycle.register(
        first, kind, identifier, TaskControl(cancelled_a.set, asyncio.Event())
    )
    token_b = await fixture.lifecycle.register(
        second, kind, identifier, TaskControl(cancelled_b.set, asyncio.Event())
    )
    assert fixture.lifecycle.lookup(first.scope, kind, identifier) is token_a
    assert fixture.lifecycle.lookup(second.scope, kind, identifier) is token_b
    for scope in ((first.visitor_id, first.generation + 1), ("normal", 0)):
        assert fixture.lifecycle.lookup(scope, kind, identifier) is None
        assert fixture.lifecycle.cancel(scope, kind, identifier) == 0
        assert not await fixture.lifecycle.pop(scope, kind, identifier, token_a)
    assert not await fixture.lifecycle.pop(*token_a.key, token_b)
    with pytest.raises(DemoError, match="DEMO_BUSY"):
        await fixture.lifecycle.pop(*token_a.key, token_a)
    with pytest.raises(DemoError, match="DEMO_BUSY"):
        await fixture.lifecycle.register(
            first, kind, identifier, TaskControl(lambda: None, asyncio.Event())
        )
    assert fixture.visitors.lease_count(first.scope) == 2
    old_token = token_a
    old_token.task_control.done.set()
    assert await fixture.lifecycle.pop(*old_token.key, old_token)
    token_a = await fixture.lifecycle.register(
        first, kind, identifier, TaskControl(cancelled_a.set, asyncio.Event())
    )
    assert not await fixture.lifecycle.pop(*old_token.key, old_token)
    assert fixture.lifecycle.lookup(*token_a.key) is token_a
    assert fixture.lifecycle.cancel_scope(first.scope) == 1
    assert cancelled_a.is_set() and not cancelled_b.is_set()
    async with fixture.visitors.transition(first.verified_visitor) as handle:
        retired = handle.mark_reset(first, str(uuid4()))
    assert retired.retired_scope == first.scope
    token_a.task_control.done.set()
    assert await fixture.lifecycle.pop(*token_a.key, token_a)
    assert not await fixture.lifecycle.pop(*token_a.key, token_a)
    assert fixture.visitors.lease_count(first.scope) == 1
    assert fixture.visitors.lease_count(second.scope) == 2
    assert fixture.lifecycle.lookup(second.scope, kind, identifier) is token_b


async def test_real_concurrent_registration_has_one_token_and_child(
    real_lifecycle,
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    barrier = asyncio.Event()

    async def register():
        await barrier.wait()
        return await fixture.lifecycle.register(
            ctx, "analysis", "same", TaskControl(lambda: None, asyncio.Event())
        )

    tasks = [asyncio.create_task(register()) for _ in range(2)]
    barrier.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [result for result in results if isinstance(result, DemoError)]
    assert len(errors) == 1 and errors[0].code == "DEMO_BUSY"
    assert len(fixture.lifecycle.tokens(ctx.scope)) == 1
    assert fixture.visitors.lease_count(ctx.scope) == 2


async def test_real_registration_failure_releases_retained_lease(
    real_lifecycle, monkeypatch
):
    import src.demo.lifecycle as module

    fixture = real_lifecycle
    ctx = await fixture.context()

    def reject_token(*args):
        raise RuntimeError("token construction rejected")

    monkeypatch.setattr(module, "ExecutionToken", reject_token)
    with pytest.raises(RuntimeError, match="token construction rejected"):
        await fixture.lifecycle.register(
            ctx, "run", 7, TaskControl(lambda: None, asyncio.Event())
        )
    assert fixture.visitors.lease_count(ctx.scope) == 1
    assert not fixture.lifecycle.tokens(ctx.scope)


async def test_real_busy_excludes_request_leases_and_includes_execution_child(
    real_lifecycle,
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    snapshot = await fixture.visitors.snapshot(ctx.verified_visitor)
    other_request = await fixture.visitors.acquire_lease(
        snapshot.ref, ctx.auth_epoch
    )
    try:
        assert fixture.visitors.lease_count(ctx.scope) == 2
        assert not await fixture.lifecycle.busy(ctx.scope)
        async with fixture.visitors.transition(ctx.verified_visitor) as handle:
            assert not fixture.lifecycle.busy_locked(handle, ctx.scope)
        async with fixture.lifecycle.child_context(ctx) as child:
            assert child.lease is not ctx.lease
            assert await fixture.lifecycle.busy(ctx.scope)
            async with fixture.visitors.transition(
                ctx.verified_visitor
            ) as handle:
                busy = fixture.lifecycle.busy_locked(handle, ctx.scope)
                same = handle.change_role(ctx, "admin", busy, user_id=1)
                assert same.auth_epoch == ctx.auth_epoch
                with pytest.raises(DemoError, match="DEMO_BUSY"):
                    handle.change_role(ctx, "teacher", busy, user_id=2)
        assert not await fixture.lifecycle.busy(ctx.scope)
        async with fixture.visitors.transition(ctx.verified_visitor) as handle:
            changed = handle.change_role(
                ctx,
                "teacher",
                fixture.lifecycle.busy_locked(handle, ctx.scope),
                user_id=2,
            )
        assert changed.auth_epoch == ctx.auth_epoch + 1
    finally:
        await fixture.visitors.release_lease(other_request)


@pytest.mark.parametrize("lease_kind", ["request", "generic", "execution"])
async def test_real_drain_waits_for_every_lease_kind(
    real_lifecycle, lease_kind
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    if lease_kind == "execution":
        async with fixture.lifecycle.child_context(ctx):
            await fixture.visitors.release_lease(ctx.lease)
            result = await fixture.lifecycle.drain(ctx.scope, 0.01)
            assert result == {"drained": False, "remaining": 1}
    else:
        lease = ctx.lease
        if lease_kind == "generic":
            lease = await fixture.visitors.retain_lease(ctx.lease)
            await fixture.visitors.release_lease(ctx.lease)
        try:
            result = await fixture.lifecycle.drain(ctx.scope, 0.01)
            assert result == {"drained": False, "remaining": 1}
        finally:
            await fixture.visitors.release_lease(lease)
    assert await fixture.lifecycle.drain(ctx.scope, 0) == {
        "drained": True,
        "remaining": 0,
    }


async def test_real_drain_reaps_token_completed_during_lease_wait(
    real_lifecycle, monkeypatch
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    token = await fixture.lifecycle.register(
        ctx, "run", "id", TaskControl(lambda: None, asyncio.Event())
    )
    await fixture.visitors.release_lease(ctx.lease)
    waiting = asyncio.Event()
    original_wait = fixture.visitors.wait_for_leases

    async def observe_wait(scope, timeout_seconds):
        waiting.set()
        return await original_wait(scope, timeout_seconds)

    monkeypatch.setattr(fixture.visitors, "wait_for_leases", observe_wait)
    task = asyncio.create_task(fixture.lifecycle.drain(ctx.scope, 1))
    await asyncio.wait_for(waiting.wait(), 2)
    token.task_control.done.set()
    assert await asyncio.wait_for(task, 2) == {"drained": True, "remaining": 0}
    assert fixture.lifecycle.lookup(*token.key) is None
    assert token.registry_lease.released


@pytest.mark.parametrize("first", ["reset", "write_guard"])
async def test_real_write_guard_and_reset_serialize_sql_without_late_writes(
    real_lifecycle, first
):
    from sqlalchemy import text

    fixture = real_lifecycle
    ctx = await fixture.context()
    factory = ctx.factory
    attempting = asyncio.Event()
    changes = []

    async def reset():
        attempting.set()
        async with fixture.visitors.transition(ctx.verified_visitor) as handle:
            reservation = handle.mark_reset(ctx, str(uuid4()))
            changes.append("reset")
            return reservation

    async with factory() as observer:
        assert (
            await observer.scalar(text("SELECT body FROM synthetic_record"))
            == "fictional"
        )
        if first == "write_guard":
            async with fixture.lifecycle.write_guard(ctx), factory() as db:
                task = asyncio.create_task(reset())
                await asyncio.wait_for(attempting.wait(), 2)
                assert not task.done() and changes == []
                await db.execute(
                    text("UPDATE synthetic_record SET body='committed'")
                )
                await db.commit()
                changes.append("commit")
            reservation = await asyncio.wait_for(task, 2)
            assert changes == ["commit", "reset"]
        else:
            reservation = await reset()
        with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
            async with fixture.lifecycle.write_guard(ctx):
                await observer.execute(
                    text("UPDATE synthetic_record SET body='late'")
                )
                await observer.commit()
        expected = "committed" if first == "write_guard" else "fictional"
        assert (
            await observer.scalar(text("SELECT body FROM synthetic_record"))
            == expected
        )
        with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
            factory()
    assert ctx.factory is factory and ctx.generation == 1
    await fixture.visitors.release_lease(ctx.lease)
    assert await fixture.lifecycle.drain(ctx.scope, 0) == {
        "drained": True,
        "remaining": 0,
    }
    await fixture.visitors.drain_retired(ctx.scope)
    candidate = await fixture.visitors.prepare(reservation.operation)
    async with fixture.visitors.transition(ctx.verified_visitor) as handle:
        published = handle.publish(reservation.operation, candidate)
    lease = await fixture.visitors.acquire_lease(
        published.ref, published.auth_epoch
    )
    try:
        current = await fixture.visitors.make_context(
            ctx.verified_visitor, lease, user_id=1
        )
        assert current.generation == ctx.generation + 1
        async with current.factory() as db:
            assert (
                await db.scalar(text("SELECT body FROM synthetic_record"))
                == "fictional"
            )
            with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
                async with fixture.lifecycle.write_guard(ctx):
                    await db.execute(
                        text("UPDATE synthetic_record SET body='late'")
                    )
                    await db.commit()
            assert (
                await db.scalar(text("SELECT body FROM synthetic_record"))
                == "fictional"
            )
        with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
            factory()
    finally:
        await fixture.visitors.release_lease(lease)


@pytest.mark.parametrize("first", ["role", "write_guard"])
async def test_real_role_and_write_guard_serialize_sql_without_stale_dml(
    real_lifecycle, first
):
    from sqlalchemy import text

    fixture = real_lifecycle
    ctx = await fixture.context()
    attempting = asyncio.Event()
    changes = []

    async def change_role():
        attempting.set()
        async with fixture.visitors.transition(ctx.verified_visitor) as handle:
            handle.change_role(
                ctx,
                "teacher",
                fixture.lifecycle.busy_locked(handle, ctx.scope),
                user_id=2,
            )
            changes.append("role")

    if first == "write_guard":
        async with fixture.lifecycle.write_guard(ctx), ctx.factory() as db:
            task = asyncio.create_task(change_role())
            await asyncio.wait_for(attempting.wait(), 2)
            assert not task.done() and changes == []
            await db.execute(
                text("UPDATE synthetic_record SET body='committed'")
            )
            await db.commit()
            changes.append("commit")
        await asyncio.wait_for(task, 2)
        assert changes == ["commit", "role"]
    else:
        await change_role()
    count = fixture.visitors.lease_count(ctx.scope)
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        await fixture.lifecycle.register(
            ctx, "analysis", "stale", TaskControl(lambda: None, asyncio.Event())
        )
    assert fixture.visitors.lease_count(ctx.scope) == count
    assert not fixture.lifecycle.tokens(ctx.scope)
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        async with fixture.lifecycle.write_guard(ctx), ctx.factory() as db:
            await db.execute(text("UPDATE synthetic_record SET body='late'"))
            await db.commit()
    async with ctx.factory() as db:
        expected = "committed" if first == "write_guard" else "fictional"
        assert (
            await db.scalar(text("SELECT body FROM synthetic_record"))
            == expected
        )
    snapshot = await fixture.visitors.snapshot(ctx.verified_visitor)
    assert (
        snapshot.role == "teacher" and snapshot.auth_epoch == ctx.auth_epoch + 1
    )


async def test_real_registration_first_blocks_role_change_but_same_role_is_noop(
    real_lifecycle,
):
    fixture = real_lifecycle
    ctx = await fixture.context()
    token = await fixture.lifecycle.register(
        ctx, "analysis", "running", TaskControl(lambda: None, asyncio.Event())
    )
    async with fixture.visitors.transition(ctx.verified_visitor) as handle:
        before = handle.snapshot()
        busy = fixture.lifecycle.busy_locked(handle, ctx.scope)
        assert busy
        same = handle.change_role(ctx, "admin", busy, user_id=1)
        assert same == before
        with pytest.raises(DemoError, match="DEMO_BUSY"):
            handle.change_role(ctx, "teacher", busy, user_id=2)
        assert handle.snapshot() == before
    assert fixture.lifecycle.tokens(ctx.scope) == (token,)
    assert fixture.visitors.lease_count(ctx.scope) == 2


@pytest.mark.parametrize("operation", ["analysis", "probe", "probe_cleanup"])
async def test_owned_execution_propagates_original_demo_error(
    monkeypatch, operation
):
    from types import SimpleNamespace

    from src.services import analysis_runs, probe_execution

    rejected = DemoError("STALE_GENERATION")

    def forbidden_factory():
        pytest.fail("Rejected execution opened a factory")

    async def reject(*args, **kwargs):
        raise rejected

    async def forbidden_cleanup(*args, **kwargs):
        pytest.fail("DemoError was converted into generic failure cleanup")

    visitors = Visitors()
    registry = Lifecycle(visitors)
    ctx = replace(visitors.context(), factory=forbidden_factory)
    released = []
    permit = SimpleNamespace(release=lambda: released.append(True))
    monkeypatch.setattr(registry, "assert_current", reject)
    if operation == "analysis":
        monkeypatch.setattr(analysis_runs, "finish_failure", forbidden_cleanup)
        call = analysis_runs.execute_analysis(
            forbidden_factory, {"run_id": "id"}, ctx=ctx, registry=registry
        )
    elif operation == "probe":
        monkeypatch.setattr(
            probe_execution, "cleanup_failure", forbidden_cleanup
        )
        call = probe_execution.run_probe(
            forbidden_factory, 7, permit, ctx=ctx, registry=registry
        )
    else:
        monkeypatch.setattr(probe_execution, "finish_probe", reject)
        call = probe_execution.cleanup_failure(
            forbidden_factory, 7, "interrupted", ctx=ctx, registry=registry
        )
    with pytest.raises(DemoError) as error:
        await call
    assert error.value is rejected
    assert released == ([True] if operation == "probe" else [])


@pytest.mark.parametrize("kind", ["analysis", "probe"])
async def test_cancelled_shutdown_owns_real_finalizer_until_session_close(
    real_lifecycle, monkeypatch, kind
):
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.services import analysis_runs, probe_execution

    fixture = real_lifecycle
    ctx = await fixture.context()
    module = analysis_runs if kind == "analysis" else probe_execution
    stop = module.stop_analyses if kind == "analysis" else module.stop_probes
    monkeypatch.setattr(module, "cleanup_tasks", set())
    monkeypatch.setattr(
        module, "active_analyses" if kind == "analysis" else "active_probes", {}
    )
    close_started, close_release, close_finished = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_close = AsyncSession.close

    async def held_close(session):
        close_started.set()
        await close_release.wait()
        try:
            await original_close(session)
        finally:
            close_finished.set()

    monkeypatch.setattr(AsyncSession, "close", held_close)
    shutdown = None
    released = []
    try:
        if kind == "analysis":
            token = await analysis_runs.start_scoped_analysis(
                ctx, {"run_id": str(uuid4())}, registry=fixture.lifecycle
            )
        else:
            permit = SimpleNamespace(
                task=None, release=lambda: released.append(True)
            )
            token = await probe_execution.start_scoped_probe(
                ctx, 7, permit, registry=fixture.lifecycle
            )
        token.request_cancel()  # No scheduling point before pre-start cancel.
        await fixture.visitors.release_lease(ctx.lease)
        await asyncio.wait_for(close_started.wait(), 5)
        assert ctx.factory.engine.pool.checkedout() == 1
        shutdown = asyncio.create_task(stop())
        await asyncio.sleep(0)  # Shutdown now waits for the held finalizer.
        for _ in range(2):
            shutdown.cancel()
            await asyncio.sleep(0)
        result = await fixture.lifecycle.drain(ctx.scope, 0.01)
        assert not token.completed
        assert fixture.lifecycle.lookup(*token.key) is token
        assert fixture.visitors.lease_count(ctx.scope) == 1
        assert result == {"drained": False, "remaining": 1}
        assert not close_finished.is_set()
        assert ctx.factory.engine.pool.checkedout() == 1
        assert not shutdown.done()
        assert module.cleanup_tasks
        close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(shutdown), 5)
        assert close_finished.is_set()
        assert ctx.factory.engine.pool.checkedout() == 0
        assert token.completed
        assert fixture.lifecycle.lookup(*token.key) is None
        assert fixture.visitors.lease_count(ctx.scope) == 0
        assert await fixture.lifecycle.drain(ctx.scope, 0) == {
            "drained": True,
            "remaining": 0,
        }
        assert not module.cleanup_tasks
        assert released == ([True] if kind == "probe" else [])
    finally:
        close_release.set()
        await asyncio.wait_for(close_finished.wait(), 5)
        if shutdown is not None:
            await asyncio.wait_for(
                asyncio.gather(shutdown, return_exceptions=True), 5
            )
        await stop()

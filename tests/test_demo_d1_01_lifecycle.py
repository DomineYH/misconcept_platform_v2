"""TTL, quota, cancellation, retirement and reset races at the public API."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from test_demo_d1_01_database import make_root
from test_demo_d1_01_visitors import ready_context

from src.demo.database import DemoError
from src.demo.visitors import ControlContext, VisitorRegistry


class FakeClock:
    def __init__(self):
        self.elapsed = 0
        self.start = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def monotonic(self):
        return self.elapsed

    def utcnow(self):
        return self.start + timedelta(seconds=self.elapsed)

    def advance(self, seconds):
        self.elapsed += seconds


class LifecycleDouble:
    """#192 cancellation/drain boundary; never substitutes for real integration."""

    def __init__(self):
        self.visitors = None
        self.events = []
        self.blocked = False

    def cancel_scope(self, scope):
        self.events.append(("cancel", scope))
        return 0

    async def drain(self, scope, timeout_seconds):
        self.events.append(("drain", scope))
        remaining = self.visitors.lease_count(scope)
        return {
            "drained": not self.blocked and remaining == 0,
            "remaining": remaining,
        }


@pytest.fixture
async def make_visitors(tmp_path):
    stores = []

    async def make(settings=None):
        path = tmp_path / str(uuid4())
        path.mkdir()
        root = await make_root(path)
        clock = FakeClock()
        lifecycle = LifecycleDouble()
        visitors = VisitorRegistry(
            root, lifecycle=lifecycle, settings=settings, clock=clock
        )
        lifecycle.visitors = visitors
        stores.append(visitors)
        return visitors, clock, lifecycle

    yield make
    for visitors in stores:
        await visitors.shutdown()
        visitors.root.close()


async def test_reset_self_lease_handoff_and_separate_child_timeout(
    make_visitors,
):
    visitors, _, lifecycle = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    child = await visitors.retain_lease(ctx.lease)
    from sqlalchemy import text

    request_db = ctx.factory()
    await request_db.execute(text("SELECT 1"))
    reset = await visitors.reset_visit(ctx, str(uuid4()))
    assert reset.snapshot.state == "resetting"
    old = ready.snapshot.ref
    # #193/#194 owned task: rollback -> session close -> own release -> drain.
    order = []
    await request_db.rollback()
    order.append("rollback")
    await request_db.close()
    order.append("close")
    await visitors.release_lease(ctx.lease)
    order.append("release")
    with pytest.raises(DemoError, match="DEMO_DRAIN_TIMEOUT"):
        await visitors.prepare(reset.operation)
    assert old.path.exists()
    assert not old.factory.disposed
    async with visitors.transition(ready.verified_visitor) as handle:
        assert handle.fail(reset.operation, "DRAIN_TIMEOUT")
    control = ControlContext(ready.verified_visitor, 2, 2)
    retry = await visitors.reset_visit(control, str(uuid4()))
    assert (retry.snapshot.generation, retry.snapshot.auth_epoch) == (2, 2)
    assert retry.operation.attempt_no == reset.operation.attempt_no + 1
    await visitors.release_lease(child)
    candidate = await visitors.prepare(retry.operation)
    assert old.factory.disposed
    async with visitors.transition(ready.verified_visitor) as handle:
        new = handle.publish(retry.operation, candidate)
    assert new.state == "ready"
    assert new.role == "admin"
    assert order == ["rollback", "close", "release"]
    assert lifecycle.events[0][0] == "cancel"
    assert lifecycle.events[1][0] == "drain"
    cleanup = await visitors.cleanup_retired()
    assert cleanup.removed == 1
    assert not old.path.exists()


async def test_role_receipts_share_idempotency_and_id_limit(make_visitors):
    visitors, _, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    request_id = str(uuid4())
    async with visitors.transition(ready.verified_visitor) as handle:
        changed = handle.change_role(
            ctx, "teacher", False, user_id=2, request_id=request_id
        )
        replay = handle.change_role(
            ctx, "teacher", False, user_id=2, request_id=request_id
        )
        assert replay is changed
        with pytest.raises(DemoError, match="DEMO_REQUEST_CONFLICT"):
            handle.change_role(
                ctx, "admin", False, user_id=1, request_id=request_id
            )
    receipt = await visitors.receipt(ready.verified_visitor, request_id)
    assert receipt.operation == "role"
    assert receipt.result.auth_epoch == 2
    await visitors.release_lease(ctx.lease)


async def test_prepare_failure_replay_retry_and_historical_receipt(
    make_visitors,
):
    visitors, clock, _ = await make_visitors()
    bootstrap = visitors.verify_bootstrap("f" * 32)
    request_id = str(uuid4())
    first = await visitors.reserve_start(bootstrap, request_id, "teacher")
    async with visitors.transition(first.verified_visitor) as handle:
        assert handle.fail(first.operation, "PREPARE_FAILED")
    deadline = (await visitors.snapshot(first.verified_visitor)).expires_at
    clock.advance(50)
    with pytest.raises(DemoError, match="DEMO_PREPARE_FAILED"):
        await visitors.create_visit(bootstrap, request_id, "teacher")
    with pytest.raises(DemoError, match="DEMO_PREPARE_FAILED"):
        await visitors.resolve_visit(first.verified_visitor, 1)
    retry = await visitors.reserve_start(bootstrap, str(uuid4()), "admin")
    assert retry.verified_visitor is first.verified_visitor
    assert (retry.snapshot.generation, retry.snapshot.auth_epoch) == (1, 1)
    assert retry.operation.attempt_no == 2
    assert retry.snapshot.expires_at == deadline
    async with visitors.transition(first.verified_visitor) as handle:
        assert not handle.fail(first.operation, "STORAGE_LIMIT")
    candidate = await visitors.prepare(retry.operation)
    async with visitors.transition(first.verified_visitor) as handle:
        ready = handle.publish(retry.operation, candidate)
    assert ready.role == "admin"
    failed = await visitors.receipt(first.verified_visitor, request_id)
    assert failed.error_code == "DEMO_PREPARE_FAILED"
    assert failed.result.state == "prepare_failed"
    assert visitors.reserved_visitors == 1


async def test_receipt_cap_preserves_existing_ids_and_never_reclones(
    make_visitors,
):
    visitors, _, _ = await make_visitors()
    bootstrap, ready, ctx = await ready_context(visitors)
    for _ in range(255):
        replay = await visitors.reserve_start(
            bootstrap, str(uuid4()), "teacher"
        )
        assert replay.snapshot.ref is ready.snapshot.ref
        assert replay.snapshot.role == "admin"
    with pytest.raises(DemoError, match="DEMO_LIMIT_REACHED"):
        await visitors.reserve_start(bootstrap, str(uuid4()), "teacher")
    replay = await visitors.create_visit(
        bootstrap, ready.operation.request_id, "admin"
    )
    assert replay.snapshot.ref is ready.snapshot.ref
    await visitors.release_lease(ctx.lease)


@pytest.mark.parametrize(
    "state",
    ["preparing", "prepare_failed", "ready", "resetting", "reset_failed"],
)
async def test_ttl_expires_all_states_without_extending_deadlines(
    make_visitors, state
):
    visitors, clock, _ = await make_visitors()
    bootstrap = visitors.verify_bootstrap("t" * 32)
    if state in ("preparing", "prepare_failed"):
        reservation = await visitors.reserve_start(
            bootstrap, str(uuid4()), "teacher"
        )
        verified = reservation.verified_visitor
        if state == "prepare_failed":
            async with visitors.transition(verified) as handle:
                handle.fail(reservation.operation, "PREPARE_FAILED")
        ctx = None
    else:
        _, reservation, ctx = await ready_context(visitors)
        verified = reservation.verified_visitor
        if state in ("resetting", "reset_failed"):
            reservation = await visitors.reset_visit(ctx, str(uuid4()))
            if state == "reset_failed":
                async with visitors.transition(verified) as handle:
                    handle.fail(reservation.operation, "RESET_FAILED")
    before = await visitors.snapshot(verified)
    assert before.state == state
    clock.advance(visitors.settings.idle_ttl_seconds)
    expired = await visitors.snapshot(verified)
    assert expired.state == "expired"
    assert (expired.generation, expired.auth_epoch) == (
        before.generation + 1,
        before.auth_epoch + 1,
    )
    assert expired.ref is None
    await visitors.expire_visit(verified)
    assert (await visitors.snapshot(verified)).generation == expired.generation
    if ctx is not None:
        with pytest.raises(DemoError, match="DEMO_EXPIRED"):
            await visitors.check(ctx)
        await visitors.release_lease(ctx.lease)
    await visitors.cleanup_tick()
    assert visitors.reserved_visitors == 0


async def test_foreground_touch_only_idle_and_absolute_wins_publish(
    make_visitors,
):
    visitors, clock, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    absolute = ready.snapshot.absolute_expires_at
    clock.advance(100)
    assert (
        await visitors.snapshot(ready.verified_visitor)
    ).last_activity_at == ready.snapshot.last_activity_at
    touched = await visitors.touch(ctx)
    assert (
        touched.idle_expires_at
        == ready.snapshot.idle_expires_at + timedelta(seconds=100)
    )
    assert touched.absolute_expires_at == absolute
    reset = await visitors.reset_visit(ctx, str(uuid4()))
    await visitors.release_lease(ctx.lease)
    candidate = await visitors.prepare(reset.operation)
    clock.advance(visitors.settings.absolute_ttl_seconds)
    async with visitors.transition(ready.verified_visitor) as handle:
        with pytest.raises(DemoError, match="DEMO_EXPIRED"):
            handle.publish(reset.operation, candidate)
        assert not handle.fail(reset.operation, "RESET_FAILED")
    assert (
        await visitors.snapshot(ready.verified_visitor)
    ).absolute_expires_at == absolute
    result = await visitors.cleanup_tick()
    assert result.pending == 0
    assert not candidate.path.exists()


async def test_final_capacity_slot_and_failed_reservation_wait_for_cleanup(
    make_visitors,
):
    from src.demo.visitors import DemoSettings

    visitors, clock, _ = await make_visitors(DemoSettings(max_visitors=1))
    bootstraps = [visitors.verify_bootstrap(str(i) * 32) for i in (1, 2)]
    gate = asyncio.Event()

    async def start(bootstrap):
        await gate.wait()
        try:
            return await visitors.reserve_start(
                bootstrap, str(uuid4()), "admin"
            )
        except DemoError as error:
            return error

    tasks = [asyncio.create_task(start(bootstrap)) for bootstrap in bootstraps]
    gate.set()
    results = await asyncio.gather(*tasks)
    winner = next(
        result for result in results if not isinstance(result, DemoError)
    )
    loser = next(result for result in results if isinstance(result, DemoError))
    assert loser.code == "DEMO_CAPACITY"
    assert loser.retry_after == 30
    async with visitors.transition(winner.verified_visitor) as handle:
        handle.fail(winner.operation, "PREPARE_FAILED")
    assert visitors.reserved_visitors == 1
    clock.advance(visitors.settings.idle_ttl_seconds)
    await visitors.cleanup_tick()
    assert visitors.reserved_visitors == 0
    winner_bootstrap = visitors.verify_bootstrap(
        "1" * 32
        if winner.verified_visitor
        is visitors.visitor_for_bootstrap(bootstraps[0])
        else "2" * 32
    )
    fresh = await visitors.reserve_start(
        winner_bootstrap, str(uuid4()), "teacher"
    )
    assert fresh.snapshot.visitor_id != winner.snapshot.visitor_id
    assert fresh.snapshot.browser_scope_id != winner.snapshot.browser_scope_id
    assert (fresh.snapshot.generation, fresh.snapshot.auth_epoch) == (1, 1)
    with pytest.raises(DemoError, match="DEMO_PREPARE_FAILED"):
        await visitors.reserve_start(
            winner_bootstrap, winner.operation.request_id, "admin"
        )


async def test_cancel_waits_through_repeated_caller_cancellation_and_reclaims_late_candidate(
    make_visitors, monkeypatch
):
    visitors, _, _ = await make_visitors()
    bootstrap = visitors.verify_bootstrap("c" * 32)
    start = await visitors.reserve_start(bootstrap, str(uuid4()), "admin")
    entered = asyncio.Event()
    finish = asyncio.Event()
    cancel_entered = asyncio.Event()
    actual_clone = visitors.root.clone
    captured = {}

    async def uncooperative_clone(visitor_id, generation, **kwargs):
        captured["signal"] = kwargs["cancel_event"]
        entered.set()
        await finish.wait()
        candidate = await actual_clone(
            visitor_id, generation, operation_id=kwargs["operation_id"]
        )
        captured["candidate"] = candidate
        return candidate

    monkeypatch.setattr(visitors.root, "clone", uncooperative_clone)
    preparing = asyncio.create_task(visitors.prepare(start.operation))
    await entered.wait()
    reserved = visitors.reserved_bytes

    async def cancel():
        cancel_entered.set()
        return await visitors.cancel_preparation(start.operation)

    cancelling = asyncio.create_task(cancel())
    await cancel_entered.wait()
    assert captured["signal"].is_set()
    cancelling.cancel()
    assert visitors.reserved_bytes == reserved
    assert not cancelling.done()

    # The second cancellation is delivered before the builder is allowed to exit.
    async def cancel_again():
        cancelling.cancel()

    await asyncio.create_task(cancel_again())
    assert visitors.reserved_bytes == reserved
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await cancelling
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        await preparing
    assert visitors.reserved_bytes == 0
    result = await visitors.cleanup_retired()
    assert result.pending == 0
    assert not captured["candidate"].path.exists()


async def test_cancelling_published_operation_preserves_ready_factory(
    make_visitors,
):
    from sqlalchemy import text

    visitors, _, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    await visitors.cancel_preparation(ready.operation)
    assert (
        await visitors.resolve_visit(ready.verified_visitor, 1)
    ) is ready.snapshot.ref
    async with ctx.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    await visitors.release_lease(ctx.lease)
    assert (await visitors.cleanup_retired()).removed == 0


async def test_cleanup_retains_capacity_and_bytes_until_lease_and_unlink(
    make_visitors, monkeypatch
):
    from src.demo.visitors import DemoSettings

    visitors, _, _ = await make_visitors(DemoSettings(max_visitors=1))
    _, ready, ctx = await ready_context(visitors)
    old = ready.snapshot.ref
    await visitors.expire_visit(ready.verified_visitor)
    blocked = await visitors.cleanup_retired()
    assert blocked.pending == 1
    assert blocked.failure_codes == ("DEMO_DRAIN_TIMEOUT",)
    assert old.path.exists()
    assert not old.factory.disposed
    assert visitors.reserved_visitors == 1
    await visitors.release_lease(ctx.lease)
    actual_cleanup = visitors.root.cleanup
    attempts = 0

    async def locked_once(ref):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DemoError("DB_LOCKED")
        return await actual_cleanup(ref)

    monkeypatch.setattr(visitors.root, "cleanup", locked_once)
    before = visitors.root.bytes_used()
    locked = await visitors.cleanup_retired()
    assert locked.pending == 1
    assert locked.failure_codes == ("DEMO_DB_LOCKED",)
    assert visitors.root.bytes_used() == before
    assert visitors.reserved_visitors == 1
    finished = await visitors.cleanup_retired()
    assert (finished.removed, finished.pending) == (1, 0)
    assert visitors.reserved_visitors == 0
    assert not old.path.exists()
    assert visitors.root.bytes_used() < before


async def test_close_failure_keeps_request_lease_and_blocks_reset(
    make_visitors, monkeypatch
):
    from sqlalchemy import text

    visitors, _, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    db = ctx.factory()
    await db.execute(text("SELECT 1"))
    reset = await visitors.reset_visit(ctx, str(uuid4()))
    actual_close = db.close

    async def fail_close():
        raise DemoError("DB_LOCKED")

    monkeypatch.setattr(db, "close", fail_close)
    await db.rollback()
    with pytest.raises(DemoError, match="DEMO_DB_LOCKED"):
        await db.close()
        await visitors.release_lease(ctx.lease)
    assert not ctx.lease.released
    assert visitors.lease_count(ctx.scope) == 1
    with pytest.raises(DemoError, match="DEMO_DRAIN_TIMEOUT"):
        await visitors.prepare(reset.operation)
    assert ready.snapshot.ref.path.exists()
    assert not ready.snapshot.ref.factory.disposed
    await actual_close()
    await visitors.release_lease(ctx.lease)


async def test_receipts_and_tombstones_expire_at_absolute_deadline(
    make_visitors,
):
    visitors, clock, _ = await make_visitors()
    bootstrap, ready, ctx = await ready_context(visitors)
    await visitors.release_lease(ctx.lease)
    clock.advance(visitors.settings.idle_ttl_seconds)
    await visitors.cleanup_tick()
    assert visitors.reserved_visitors == 0
    assert visitors.visitor_for_bootstrap(bootstrap) is ready.verified_visitor
    clock.advance(visitors.settings.absolute_ttl_seconds)
    await visitors.cleanup_tick()
    renewed_bootstrap = visitors.verify_bootstrap(bootstrap.bootstrap_nonce)
    assert visitors.visitor_for_bootstrap(renewed_bootstrap) is None
    assert not await visitors.release_lease(ctx.lease)
    with pytest.raises(DemoError, match="DEMO_SESSION_REQUIRED"):
        await visitors.resolve_visit(ready.verified_visitor, 1)


async def test_backlog_blocks_new_work_but_keeps_ready_reads_and_releases(
    make_visitors, monkeypatch
):
    from sqlalchemy import text

    from src.demo.visitors import DemoSettings

    visitors, _, _ = await make_visitors(
        DemoSettings(max_retired_generations=1)
    )
    _, ready, ctx = await ready_context(visitors)
    reset = await visitors.reset_visit(ctx, str(uuid4()))
    await visitors.release_lease(ctx.lease)
    candidate = await visitors.prepare(reset.operation)
    async with visitors.transition(ready.verified_visitor) as handle:
        current = handle.publish(reset.operation, candidate)
    other_bootstrap = visitors.verify_bootstrap("o" * 32)
    with pytest.raises(DemoError, match="DEMO_CLEANUP_BACKLOG") as error:
        await visitors.reserve_start(other_bootstrap, str(uuid4()), "admin")
    assert error.value.retry_after == visitors.settings.cleanup_interval_seconds
    ref = await visitors.resolve_visit(ready.verified_visitor, 2)
    lease = await visitors.acquire_lease(ref, 2)
    current_ctx = await visitors.make_context(
        ready.verified_visitor, lease, user_id=1
    )
    async with ref.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    with pytest.raises(DemoError, match="DEMO_CLEANUP_BACKLOG"):
        await visitors.reset_visit(current_ctx, str(uuid4()))
    assert (await visitors.snapshot(ready.verified_visitor)) == current
    await visitors.release_lease(lease)
    assert (await visitors.cleanup_retired()).removed == 1


async def test_restart_queues_verified_orphan_and_never_resumes_old_reference(
    make_visitors,
):
    from src.demo.database import DatabaseRoot

    visitors, _, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    await visitors.release_lease(ctx.lease)
    old = ready.snapshot.ref
    await old.factory.dispose()
    visitors.root.close()
    root = DatabaseRoot(visitors.root.path).open()
    root.validate_seed(visitors.root._seed)
    clock = FakeClock()
    lifecycle = LifecycleDouble()
    restarted = VisitorRegistry(root, lifecycle=lifecycle, clock=clock)
    lifecycle.visitors = restarted
    try:
        with pytest.raises(DemoError, match="DEMO_SESSION_REQUIRED"):
            await restarted.resolve_visit(ready.verified_visitor, 1)
        assert restarted.reserved_visitors == 1
        result = await restarted.cleanup_retired()
        assert result.removed == 1
        assert restarted.reserved_visitors == 0
        assert not old.path.exists()
    finally:
        root.close()
        # Restore the original test-store lock for fixture shutdown.
        visitors.root.open()


async def test_check_error_priority_and_reset_receipts(make_visitors):
    from dataclasses import replace

    visitors, clock, _ = await make_visitors()
    _, ready, ctx = await ready_context(visitors)
    request_id = str(uuid4())
    reset = await visitors.reset_visit(ctx, request_id)
    replay = await visitors.reset_visit(ctx, request_id)
    assert replay.operation is reset.operation
    assert (replay.snapshot.generation, replay.snapshot.auth_epoch) == (2, 2)
    with pytest.raises(DemoError, match="DEMO_RESET_IN_PROGRESS"):
        await visitors.reset_visit(ctx, str(uuid4()))
    with pytest.raises(DemoError, match="DEMO_REQUEST_CONFLICT"):
        await visitors.reset_visit(
            ControlContext(ready.verified_visitor, 2, 2), request_id
        )
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        await visitors.check(replace(ctx, auth_epoch=99))
    with pytest.raises(DemoError, match="DEMO_RESET_IN_PROGRESS"):
        await visitors.check(replace(ctx, generation=2, auth_epoch=99))
    async with visitors.transition(ready.verified_visitor) as handle:
        handle.fail(reset.operation, "DB_LOCKED")
    with pytest.raises(DemoError, match="DEMO_DB_LOCKED"):
        await visitors.reset_visit(ctx, request_id)
    with pytest.raises(DemoError, match="DEMO_RESET_FAILED"):
        await visitors.check(replace(ctx, generation=2, auth_epoch=99))
    clock.advance(visitors.settings.idle_ttl_seconds)
    with pytest.raises(DemoError, match="DEMO_EXPIRED"):
        await visitors.check(replace(ctx, generation=99, auth_epoch=99))
    await visitors.release_lease(ctx.lease)


async def test_foreign_candidate_never_retires_another_ready_visit(
    make_visitors,
):
    from sqlalchemy import text

    visitors, _, _ = await make_visitors()
    _, other, other_ctx = await ready_context(visitors)
    bootstrap = visitors.verify_bootstrap("foreign-candidate-test-bootstrap")
    first = await visitors.reserve_start(bootstrap, str(uuid4()), "admin")
    async with visitors.transition(first.verified_visitor) as handle:
        with pytest.raises(DemoError, match="DEMO_VERSION_MISMATCH"):
            handle.publish(first.operation, other.snapshot.ref)
        handle.fail(first.operation, "VERSION_MISMATCH")
    await visitors.cleanup_retired()
    async with other_ctx.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    assert (
        await visitors.resolve_visit(other.verified_visitor, 1)
    ) is other.snapshot.ref
    await visitors.release_lease(other_ctx.lease)


@pytest.mark.parametrize(
    "code,status,retryable,recovery,retry_after",
    [
        ("SESSION_REQUIRED", 401, False, "start_required", None),
        ("EXPIRED", 410, False, "expired", None),
        ("STALE_GENERATION", 409, False, "stale_generation", None),
        ("STALE_ROLE", 409, False, "stale_role", None),
        ("START_IN_PROGRESS", 409, False, "preparing", None),
        ("RESET_IN_PROGRESS", 409, False, "resetting", None),
        ("PREPARE_FAILED", 503, True, "prepare_failed", None),
        ("RESET_FAILED", 503, True, "reset_failed", None),
        ("DRAIN_TIMEOUT", 503, True, "reset_failed", None),
        ("DB_LOCKED", 503, True, "retry_later", 1),
        ("ROLE_DELETED", 409, False, "role_deleted", None),
        ("BUSY", 409, True, "wait_or_stop", None),
        ("LIMIT_REACHED", 429, False, "limit_reached", None),
        ("CAPACITY", 503, True, "capacity", 30),
        ("STORAGE_LIMIT", 507, False, "storage_limit", None),
        ("CLEANUP_BACKLOG", 503, True, "retry_later", 17),
        ("VERSION_MISMATCH", 503, False, "unavailable", None),
        ("CONFIGURATION_INVALID", 503, False, "unavailable", None),
        ("INVALID_REQUEST", 422, False, "check_input", None),
        ("REQUEST_CONFLICT", 409, False, "refresh_state", None),
    ],
)
def test_demo_error_transport_contract(
    code, status, retryable, recovery, retry_after
):
    error = DemoError(code, retry_after=retry_after)
    assert error.code == "DEMO_" + code
    assert error.status_code == error.status == status
    assert error.retryable is retryable
    assert error.recovery == "demo." + recovery
    assert error.retry_after == retry_after
    assert error.detail["generation"] is error.detail["auth_epoch"] is None
    assert str(error) == error.code


async def test_historical_start_replay_checks_new_visit_ttl_under_transition(
    make_visitors,
):
    visitors, clock, _ = await make_visitors()
    bootstrap, first, ctx = await ready_context(visitors)
    await visitors.release_lease(ctx.lease)
    clock.advance(visitors.settings.idle_ttl_seconds)
    await visitors.cleanup_tick()
    second = await visitors.create_visit(bootstrap, str(uuid4()), "teacher")
    assert second.verified_visitor is not first.verified_visitor
    clock.advance(visitors.settings.idle_ttl_seconds)
    with pytest.raises(DemoError, match="DEMO_EXPIRED"):
        await visitors.reserve_start(
            bootstrap, first.operation.request_id, "admin"
        )
    assert (await visitors.snapshot(second.verified_visitor)).state == "expired"


async def test_failed_staging_cleanup_keeps_slot_and_reservation_until_actual_unlink(
    make_visitors, monkeypatch
):
    from dataclasses import replace
    from pathlib import Path

    visitors, clock, _ = await make_visitors()
    spec = visitors.root._seed
    actual_verify = spec.verify_content

    def fail_destination(db, manifest):
        actual_verify(db, manifest)
        if (
            db.execute("SELECT kind FROM demo_metadata").fetchone()[0]
            == "demo_visitor"
        ):
            raise DemoError("VERSION_MISMATCH")

    visitors.root.validate_seed(replace(spec, verify_content=fail_destination))
    actual_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path.parent == visitors.root.path and path.suffix == ".staging":
            raise PermissionError("fictional locked candidate")
        return actual_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    bootstrap = visitors.verify_bootstrap("locked-staging-reservation-test00")
    request_id = str(uuid4())
    with pytest.raises(DemoError, match="DEMO_DB_LOCKED"):
        await visitors.create_visit(bootstrap, request_id, "admin")
    assert visitors.reserved_visitors == 1
    assert visitors.reserved_bytes == visitors.settings.max_db_bytes
    assert len(visitors.root.pending_operations) == 1
    clock.advance(visitors.settings.idle_ttl_seconds)
    blocked = await visitors.cleanup_tick()
    assert blocked.pending == 1
    assert visitors.reserved_visitors == 1
    assert visitors.reserved_bytes == visitors.settings.max_db_bytes
    monkeypatch.setattr(Path, "unlink", actual_unlink)
    finished = await visitors.cleanup_tick()
    assert finished.pending == 0
    assert visitors.reserved_visitors == visitors.reserved_bytes == 0
    assert not list(visitors.root.path.glob("*.staging"))


async def test_expired_pending_start_receipt_stays_terminal_after_new_visit(
    make_visitors,
):
    visitors, clock, _ = await make_visitors()
    bootstrap = visitors.verify_bootstrap("expired-pending-start-bootstrap00")
    first = await visitors.reserve_start(bootstrap, str(uuid4()), "admin")
    clock.advance(visitors.settings.idle_ttl_seconds)
    await visitors.cleanup_tick()
    second = await visitors.create_visit(bootstrap, str(uuid4()), "teacher")
    assert second.snapshot.state == "ready"
    with pytest.raises(DemoError, match="DEMO_EXPIRED"):
        await visitors.reserve_start(
            bootstrap, first.operation.request_id, "admin"
        )


async def test_shutdown_blocks_other_visits_while_waiting_for_cancelled_builder(
    make_visitors, monkeypatch
):
    visitors, _, lifecycle = await make_visitors()
    bootstrap = visitors.verify_bootstrap("shutdown-cancel-builder-bootstrap")
    first = await visitors.reserve_start(bootstrap, str(uuid4()), "admin")
    _, ready, ctx = await ready_context(visitors)
    await visitors.release_lease(ctx.lease)
    entered = asyncio.Event()
    finish = asyncio.Event()
    closing = asyncio.Event()
    actual_clone = visitors.root.clone
    actual_cancel = lifecycle.cancel_scope

    async def delayed_clone(visitor_id, generation, **kwargs):
        entered.set()
        await finish.wait()
        return await actual_clone(visitor_id, generation, **kwargs)

    def cancel_scope(scope):
        closing.set()
        return actual_cancel(scope)

    monkeypatch.setattr(visitors.root, "clone", delayed_clone)
    monkeypatch.setattr(lifecycle, "cancel_scope", cancel_scope)
    preparing = asyncio.create_task(visitors.prepare(first.operation))
    await entered.wait()
    shutdown = asyncio.create_task(visitors.shutdown())
    await closing.wait()
    try:
        with pytest.raises(DemoError, match="DEMO_EXPIRED"):
            await visitors.acquire_lease(
                ready.snapshot.ref, ready.snapshot.auth_epoch
            )
    finally:
        finish.set()
        await shutdown
        with pytest.raises(DemoError):
            await preparing

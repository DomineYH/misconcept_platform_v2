"""Public visitor admission, transition, lease and guarded storage contracts."""

import pytest

from src.demo.database import DemoError
from src.demo.visitors import DemoSettings


@pytest.mark.parametrize(
    "name,value",
    [
        ("workers", True),
        ("workers", 2),
        ("idle_ttl_seconds", 299),
        ("absolute_ttl_seconds", 28801),
        ("max_visitors", 0),
        ("max_visitors", 1.0),
        ("max_db_bytes", -1),
        ("max_disk_bytes", False),
        ("drain_seconds", 31),
        ("cleanup_interval_seconds", 4),
        ("max_retired_generations", 2001),
    ],
)
def test_settings_reject_non_integer_and_out_of_range(name, value):
    with pytest.raises(DemoError) as error:
        DemoSettings(**{name: value}).validate(seed_bytes=1024)
    assert error.value.code == "DEMO_CONFIGURATION_INVALID"


def test_settings_cross_validate_seed_disk_and_deadlines():
    DemoSettings().validate(seed_bytes=1024)
    for settings, seed_bytes in (
        (DemoSettings(idle_ttl_seconds=7200, absolute_ttl_seconds=1800), 1),
        (DemoSettings(max_db_bytes=1048576), 1048577),
        (DemoSettings(max_disk_bytes=67108864), 1024),
    ):
        with pytest.raises(DemoError) as error:
            settings.validate(seed_bytes=seed_bytes)
        assert error.value.code == "DEMO_CONFIGURATION_INVALID"


@pytest.fixture
async def visitors(tmp_path):
    from test_demo_d1_01_database import make_root

    from src.demo.visitors import VisitorRegistry

    class IdleLifecycle:
        def cancel_scope(self, scope):
            return 0

        async def drain(self, scope, timeout_seconds):
            return {"drained": True, "remaining": 0}

    root = await make_root(tmp_path)
    registry = VisitorRegistry(root, lifecycle=IdleLifecycle())
    yield registry
    await registry.shutdown()
    root.close()


async def test_bootstrap_admission_reserves_only_one_leader(visitors):
    import asyncio
    from uuid import uuid4

    bootstrap = visitors.verify_bootstrap("b" * 32)
    barrier = asyncio.Event()
    ids = [str(uuid4()), str(uuid4())]

    async def reserve(request_id, role):
        await barrier.wait()
        try:
            return await visitors.reserve_start(bootstrap, request_id, role)
        except DemoError as error:
            return error

    tasks = [
        asyncio.create_task(reserve(ids[0], "teacher")),
        asyncio.create_task(reserve(ids[1], "admin")),
    ]
    barrier.set()
    results = await asyncio.gather(*tasks)
    winners = [
        result for result in results if not isinstance(result, DemoError)
    ]
    losers = [result for result in results if isinstance(result, DemoError)]
    assert len(winners) == len(losers) == 1
    result = winners[0]
    assert result.leader
    assert result.snapshot.state == "preparing"
    assert result.snapshot.ref is None
    assert (result.snapshot.generation, result.snapshot.auth_epoch) == (1, 1)
    assert losers[0].code == "DEMO_START_IN_PROGRESS"
    replay = await visitors.reserve_start(
        bootstrap,
        result.operation.request_id,
        "teacher" if result.operation.request_id == ids[0] else "admin",
    )
    assert not replay.leader
    assert replay.operation is result.operation
    with pytest.raises(DemoError, match="DEMO_REQUEST_CONFLICT"):
        await visitors.reserve_start(
            bootstrap,
            result.operation.request_id,
            "admin" if result.operation.request_id == ids[0] else "teacher",
        )
    assert visitors.reserved_visitors == 1


async def ready_context(visitors, role="admin"):
    import secrets
    from uuid import uuid4

    bootstrap = visitors.verify_bootstrap(secrets.token_hex(16))
    ready = await visitors.create_visit(bootstrap, str(uuid4()), role)
    lease = await visitors.acquire_lease(
        ready.snapshot.ref, ready.snapshot.auth_epoch
    )
    ctx = await visitors.make_context(
        ready.verified_visitor, lease, user_id=1 if role == "admin" else 2
    )
    return bootstrap, ready, ctx


async def test_ready_replay_factory_isolation_and_server_capabilities(visitors):
    from dataclasses import replace
    from uuid import uuid4

    from sqlalchemy import text

    from src.demo.visitors import VerifiedVisitor

    bootstrap, ready, ctx = await ready_context(visitors, "teacher")
    _, other, other_ctx = await ready_context(visitors)
    assert ready.snapshot.ref.factory is not other.snapshot.ref.factory
    assert (
        ready.snapshot.ref.factory.engine
        is not other.snapshot.ref.factory.engine
    )
    async with ctx.factory() as db:
        async with visitors.write_guard(ctx):
            await db.execute(
                text("UPDATE synthetic_record SET body='only A' WHERE id=1")
            )
            await db.commit()
    async with other_ctx.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    replay = await visitors.create_visit(
        bootstrap, ready.operation.request_id, "teacher"
    )
    current = await visitors.create_visit(bootstrap, str(uuid4()), "admin")
    assert replay.snapshot.ref is current.snapshot.ref is ready.snapshot.ref
    assert current.snapshot.role == "teacher"
    assert visitors.reserved_visitors == 2
    for forged in (ctx.visitor_id, VerifiedVisitor(ctx.visitor_id, object())):
        with pytest.raises(DemoError, match="DEMO_SESSION_REQUIRED"):
            await visitors.resolve_visit(forged, 1)
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        await visitors.acquire_lease(replace(ready.snapshot.ref), 1)
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        await visitors.resolve_visit(ready.verified_visitor, True)
    await visitors.release_lease(ctx.lease)
    await visitors.release_lease(other_ctx.lease)


async def test_handle_and_unique_child_leases_do_not_reenter_lock(visitors):
    from dataclasses import FrozenInstanceError

    _, ready, ctx = await ready_context(visitors)
    scope = ctx.scope
    assert visitors.lease_count(scope) == 1
    async with visitors.transition(ready.verified_visitor) as handle:
        assert handle.check(ctx).ref is ready.snapshot.ref
        child = handle.retain_lease(ctx.lease)
        assert child.lease_id != ctx.lease.lease_id
        assert visitors.lease_count(scope) == 2
        assert handle.release_lease(child)
        assert not handle.release_lease(child)
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await visitors.retain_lease(ctx.lease)
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await _foreign_check(handle, ctx)
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        handle.snapshot()
    with pytest.raises(FrozenInstanceError):
        ctx.auth_epoch = 99
    child = await visitors.retain_lease(ctx.lease)
    assert visitors.lease_count(scope) == 2
    await visitors.release_lease(child)
    assert await visitors.retain_lease(None, app_mode="normal") is None
    assert await visitors.release_lease(None, app_mode="normal") is None
    for operation in (visitors.retain_lease, visitors.release_lease):
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await operation(None)
    await visitors.release_lease(ctx.lease)
    assert visitors.lease_count(scope) == 0
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        await visitors.check(ctx)


async def _foreign_check(handle, ctx):
    import asyncio

    async def check():
        return handle.check(ctx)

    return await asyncio.create_task(check())


async def test_guard_serializes_commit_before_reset_and_rejects_old_generation(
    visitors,
):
    import asyncio
    from uuid import uuid4

    from sqlalchemy import text

    _, ready, ctx = await ready_context(visitors)
    acquired = asyncio.Event()
    reset_started = asyncio.Event()
    allow_commit = asyncio.Event()
    order = []

    async def writer():
        async with ctx.factory() as db:
            async with visitors.write_guard(ctx):
                acquired.set()
                await allow_commit.wait()
                await db.execute(
                    text("UPDATE synthetic_record SET body='old write'")
                )
                await db.commit()
                order.append("commit")

    async def reset():
        await acquired.wait()
        reset_started.set()
        result = await visitors.reset_visit(ctx, str(uuid4()))
        order.append("mark")
        return result

    write_task = asyncio.create_task(writer())
    reset_task = asyncio.create_task(reset())
    await reset_started.wait()
    allow_commit.set()
    await write_task
    reset = await reset_task
    assert order == ["commit", "mark"]
    assert reset.snapshot.state == "resetting"
    assert reset.snapshot.ref is None
    assert (reset.snapshot.generation, reset.snapshot.auth_epoch) == (2, 2)
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        async with visitors.write_guard(ctx):
            pytest.fail("old generation was allowed to write")
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        await visitors.retain_lease(ctx.lease)
    await visitors.release_lease(ctx.lease)
    candidate = await visitors.prepare(reset.operation)
    async with visitors.transition(ready.verified_visitor) as handle:
        new = handle.publish(reset.operation, candidate)
    assert new.role == "admin"
    async with new.ref.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    with pytest.raises(DemoError):
        ready.snapshot.ref.factory()
    await visitors.release_lease(ctx.lease)
    assert visitors.lease_count(ctx.scope) == 0


async def test_role_change_and_raw_factory_writes_share_guard_authority(
    visitors,
):
    from sqlalchemy import text

    _, ready, ctx = await ready_context(visitors)
    async with ctx.factory() as db:
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await db.execute(text("UPDATE synthetic_record SET body='bypass'"))
        await db.rollback()
    async with visitors.transition(ready.verified_visitor) as handle:
        same = handle.change_role(ctx, "admin", True)
        assert (same.auth_epoch, same.csrf_revision) == (1, 1)
        with pytest.raises(DemoError, match="DEMO_BUSY"):
            handle.change_role(ctx, "teacher", True, user_id=2)
        with pytest.raises(DemoError, match="DEMO_ROLE_DELETED"):
            handle.change_role(ctx, "teacher", False)
        changed = handle.change_role(ctx, "teacher", False, user_id=2)
        assert (changed.auth_epoch, changed.csrf_revision) == (2, 2)
        with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
            handle.change_role(ctx, "teacher", True, user_id=2)
        with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
            handle.retain_lease(ctx.lease)
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        async with visitors.write_guard(ctx):
            pytest.fail("old role was allowed to write")
    async with ctx.factory() as db:
        assert (
            await db.execute(text("SELECT body FROM synthetic_record"))
        ).scalar_one() == "fictional"
    await visitors.release_lease(ctx.lease)

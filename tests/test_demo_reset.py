"""#203 reset race: write barrier, replay, expiry, drain, restart (contract 5.1-9.1)."""

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_demo_d1_03_session import (
    harness as session_harness,  # noqa: F401
)
from test_demo_d1_03_session import (  # noqa: F401
    make_root,
    reset,
    seed_template,
    start,
    uid,
)

from src.demo.database import DatabaseRoot, DemoError
from src.demo.visitors import VisitorRegistry


@pytest.fixture
def h(session_harness):  # noqa: F811
    """The #193 control harness: real registry/lifecycle/session over HTTP."""
    return session_harness


def rows(path):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return db.execute("SELECT COUNT(*) FROM synthetic_record").fetchone()[0]


def visitor_files(root):
    return sorted(root.path.glob("*-g*-*.db"))


async def ready(h):
    await start(h)
    await h.client.get("/demo/state")
    return h.visitors.verified("A")


async def live_context(h, visitor):
    """A request-like lease on the current generation, separate from HTTP."""
    snap = await h.visitors.snapshot(visitor)
    lease = await h.visitors.acquire_lease(snap.ref, snap.auth_epoch)
    user = 1 if snap.role == "admin" else 2
    return await h.visitors.make_context(visitor, lease, user_id=user)


async def insert(ctx, row_id, body):
    async with ctx.factory() as db:
        await db.execute(
            text("INSERT INTO synthetic_record VALUES(:i,:b)"),
            {"i": row_id, "b": body},
        )
        await db.commit()


async def finish_reset(h, visitor, reservation):
    """Lease-free rest of a mark_reset: drain, clone, publish (5.1 flow)."""
    candidate = await h.visitors.prepare(reservation.operation)
    async with h.visitors.transition(visitor) as handle:
        return candidate, handle.publish(reservation.operation, candidate)


# -- 5.1 barrier -----------------------------------------------------------


async def test_commit_inside_write_guard_precedes_reset(h):
    visitor = await ready(h)
    ctx = await live_context(h, visitor)
    old_path, attempting = ctx.factory.path, asyncio.Event()

    async def mark():
        attempting.set()
        return await h.visitors.reset_visit(ctx, uid())

    async with h.lifecycle.write_guard(ctx):
        task = asyncio.create_task(mark())
        await attempting.wait()
        assert not task.done()  # reset waits for the transition lock
        await insert(ctx, 2, "committed")
    reservation = await asyncio.wait_for(task, 5)
    assert rows(old_path) == 2  # exactly one commit landed in the old DB
    await h.visitors.release_lease(ctx.lease)
    candidate, published = await finish_reset(h, visitor, reservation)
    assert published.generation == 2 and rows(candidate.path) == 1
    await h.visitors.cleanup_retired()
    assert not old_path.exists()


async def test_reset_first_rejects_late_write_and_never_recreates_old_db(h):
    visitor = await ready(h)
    ctx = await live_context(h, visitor)
    old_path, attempting = ctx.factory.path, asyncio.Event()
    touched = []

    async def late_write():
        attempting.set()
        async with h.lifecycle.write_guard(ctx):
            touched.append("entered")
            await insert(ctx, 2, "late")

    async with h.visitors.transition(visitor) as handle:
        waiter = asyncio.create_task(late_write())
        await attempting.wait()  # blocked on the lock we hold
        reservation = handle.mark_reset(ctx, uid())
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        await waiter
    assert touched == [] and rows(old_path) == 1
    await h.visitors.release_lease(ctx.lease)
    candidate, published = await finish_reset(h, visitor, reservation)
    assert rows(candidate.path) == 1
    # Stale publish must not swap the factory that is already ready.
    async with h.visitors.transition(visitor) as handle:
        with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
            handle.publish(reservation.operation, candidate)
        assert handle.snapshot().ref is published.ref
    assert not published.ref.factory.retired
    await h.visitors.cleanup_retired()
    assert not old_path.exists()
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        async with h.lifecycle.write_guard(ctx):
            touched.append("after cleanup")
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        ctx.factory()
    assert touched == [] and not old_path.exists()
    assert rows(candidate.path) == 1
    assert visitor_files(h.root) == [candidate.path]


# -- 7 / 9.1 duplicate and concurrent reset --------------------------------


async def test_duplicate_reset_increments_generation_once(h):
    visitor = await ready(h)
    h.visitors.clone_started.clear()
    h.visitors.clone_continue.clear()
    rid = uid()
    first = asyncio.create_task(reset(h, rid))
    await h.visitors.clone_started.wait()
    replay = await reset(h, rid)
    assert replay.status_code == 202 and replay.json()["state"] == "resetting"
    assert replay.json()["generation"] == 2
    assert "set-cookie" not in replay.headers
    other = await reset(h, generation=2, epoch=2)
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "DEMO_RESET_IN_PROGRESS"
    # 5.1 order puts STALE_GENERATION before the state error for old g/epoch.
    stale = await reset(h)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "DEMO_STALE_GENERATION"
    h.visitors.clone_continue.set()
    done = await asyncio.wait_for(first, 10)
    assert done.status_code == 200 and done.json()["state"] == "ready"
    assert done.json()["generation"] == done.json()["auth_epoch"] == 2
    await h.client.get("/demo/state")
    again = await reset(h, rid)
    assert again.status_code == 200
    assert again.json()["result"]["generation"] == 2
    assert not again.json()["historical"]
    assert "set-cookie" not in again.headers
    snap = await h.visitors.snapshot(visitor)
    assert (snap.generation, snap.auth_epoch) == (2, 2)
    assert h.visitors.clones == 2  # one start + one reset clone


# -- 7 expiry vs reset -----------------------------------------------------


async def test_ttl_during_reset_preparation_beats_publish(h):
    visitor = await ready(h)
    h.visitors.clone_started.clear()
    h.visitors.clone_continue.clear()
    rid = uid()
    caller = asyncio.create_task(reset(h, rid))
    await h.visitors.clone_started.wait()
    h.visitors.clock.elapsed = 1800  # now >= expires_at (idle 1800 s)
    h.visitors.clone_continue.set()
    response = await asyncio.wait_for(caller, 10)
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "DEMO_EXPIRED"
    snap = await h.visitors.snapshot(visitor)
    assert snap.state == "expired" and snap.ref is None
    assert snap.generation == 3  # tombstone: reserved g2 was bumped again
    for call in (
        h.client.get("/demo/state"),
        h.client.get("/demo/state", params={"request_id": rid}),
        reset(h, generation=2, epoch=2),
    ):
        late = await call
        assert late.status_code == 410
        assert late.json()["detail"]["code"] == "DEMO_EXPIRED"
    await h.visitors.cleanup_retired()
    assert visitor_files(h.root) == []  # neither old nor candidate kept


# -- 7 / 4 restart orphan cleanup -----------------------------------------


class IdleLifecycle:
    def cancel_scope(self, scope):
        return 0

    async def drain(self, scope, timeout_seconds):
        return {"drained": True, "remaining": 0}


@pytest.mark.parametrize(
    "name,payload,code",
    [
        ("notes.txt", b"not a generation", "DEMO_CONFIGURATION_INVALID"),
        (
            f"{uuid4()}-g1-{uuid4()}.db",
            b"not sqlite at all",
            "DEMO_VERSION_MISMATCH",
        ),
    ],
)
async def test_restart_removes_old_generation_and_rejects_unknown_files(
    tmp_path, name, payload, code
):
    root = await make_root(tmp_path)
    seed = root._seed
    registry = VisitorRegistry(root, lifecycle=IdleLifecycle())
    nonce = "a" * 32
    first = await registry.create_visit(
        registry.verify_bootstrap(nonce), uid(), "admin"
    )
    old_path = first.snapshot.ref.path
    await registry.acquire_lease(first.snapshot.ref, 1)  # live at shutdown
    await registry.shutdown()
    assert old_path.exists()  # leased file survives shutdown
    root.close()

    restarted = DatabaseRoot(root.path).open()
    try:
        restarted.validate_seed(seed)
        stray = restarted.path / name
        stray.write_bytes(payload)
        with pytest.raises(DemoError) as error:
            VisitorRegistry(restarted, lifecycle=IdleLifecycle())
        assert error.value.code == code
        assert stray.read_bytes() == payload  # isolated, not adopted or deleted
        stray.unlink()
        fresh = VisitorRegistry(restarted, lifecycle=IdleLifecycle())
        bootstrap = fresh.verify_bootstrap(nonce)
        assert fresh.visitor_for_bootstrap(bootstrap) is None  # no old data
        assert fresh.pending_cleanup_count == 1 and old_path.exists()
        result = await fresh.cleanup_retired()
        assert (result.removed, result.pending) == (1, 0)
        assert not old_path.exists()
        second = await fresh.create_visit(bootstrap, uid(), "admin")
        assert second.snapshot.ref.path != old_path
        assert second.snapshot.visitor_id != first.snapshot.visitor_id
        await fresh.shutdown()
    finally:
        restarted.close()


# -- 8 / 11 locked, storage limit, request-id cap ---------------------------


async def test_locked_seed_fails_reset_safely_and_new_id_retries(h):
    visitor = await ready(h)
    seed = h.root.path / "seed.db"
    blocker = sqlite3.connect(seed, timeout=0, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        rid = uid()
        failed = await reset(h, rid)
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "DEMO_DB_LOCKED"
        assert failed.headers["retry-after"] == "1"
        state = await h.client.get("/demo/state")
        assert state.json()["state"] == "reset_failed"
        assert state.json()["generation"] == state.json()["auth_epoch"] == 2
        assert state.json()["last_error"] == "DEMO_DB_LOCKED"
        snap = await h.visitors.snapshot(visitor)
        assert snap.ref is None  # never rolls back to the old DB
        replay = await reset(h, rid)
        assert replay.status_code == 503 and h.visitors.clones == 2
    finally:
        blocker.rollback()
        blocker.close()
    retry = await reset(h, generation=2, epoch=2)
    assert retry.status_code == 200
    assert retry.json()["generation"] == retry.json()["auth_epoch"] == 2


async def test_locked_start_is_not_published_and_new_id_retries(h):
    seed = h.root.path / "seed.db"
    blocker = sqlite3.connect(seed, timeout=0, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        failed = await start(h, "admin")
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "DEMO_DB_LOCKED"
        assert failed.headers["retry-after"] == "1"
        state = (await h.client.get("/demo/state")).json()
        assert state["state"] == "prepare_failed" and state["role"] is None
        assert state["next_url"] is None
    finally:
        blocker.rollback()
        blocker.close()
    assert visitor_files(h.root) == []
    await h.client.get("/demo/state")
    retry = await start(h, "admin")
    assert retry.status_code == 201
    assert retry.json()["generation"] == 1  # same visitor, same g=1


async def test_locked_retired_file_is_kept_until_unlocked(h):
    visitor = await ready(h)
    ctx = await live_context(h, visitor)
    old_path = ctx.factory.path
    reservation = await h.visitors.reset_visit(ctx, uid())
    await h.visitors.release_lease(ctx.lease)
    await finish_reset(h, visitor, reservation)
    blocker = sqlite3.connect(old_path, timeout=0, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        result = await h.visitors.cleanup_retired()
        assert "DEMO_DB_LOCKED" in result.failure_codes
        assert result.pending >= 1 and old_path.exists()
    finally:
        blocker.rollback()
        blocker.close()
    result = await h.visitors.cleanup_retired()
    assert result.pending == 0 and not old_path.exists()


async def test_storage_limit_rejects_reset_and_start_without_state_change(h):
    from dataclasses import replace

    visitor = await ready(h)
    settings = h.visitors.settings
    used = h.root.bytes_used() + h.root.external_seed_bytes
    h.visitors.settings = replace(
        settings, max_disk_bytes=used + settings.max_db_bytes - 1
    )
    refused = await reset(h)
    assert refused.status_code == 507
    assert refused.json()["detail"]["code"] == "DEMO_STORAGE_LIMIT"
    snap = await h.visitors.snapshot(visitor)
    assert (snap.state, snap.generation, snap.auth_epoch) == ("ready", 1, 1)
    assert snap.ref.path.exists() and h.visitors.lease_count(snap.scope) == 0
    other = h.visitors.bootstraps["B"]
    with pytest.raises(DemoError) as error:
        await h.visitors.reserve_start(other, uid(), "admin")
    assert error.value.code == "DEMO_STORAGE_LIMIT"
    assert error.value.status_code == 507
    assert h.visitors.visitor_for_bootstrap(other) is None
    assert h.visitors.reserved_visitors == 1
    h.visitors.settings = settings
    await h.client.get("/demo/state")
    ok = await reset(h)
    assert ok.status_code == 200 and ok.json()["generation"] == 2


async def test_257th_distinct_request_id_is_limit_reached_without_eviction(h):
    visitor = await ready(h)
    bootstrap = h.visitors.bootstraps["A"]
    ids = [uid() for _ in range(255)]  # plus the start id = 256 receipts
    for request_id in ids:
        await h.visitors.reserve_start(bootstrap, request_id, "teacher")
    with pytest.raises(DemoError) as error:
        await h.visitors.reserve_start(bootstrap, uid(), "teacher")
    assert error.value.code == "DEMO_LIMIT_REACHED"
    assert error.value.status_code == 429
    refused = await reset(h)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "DEMO_LIMIT_REACHED"
    snap = await h.visitors.snapshot(visitor)
    assert (snap.state, snap.generation) == ("ready", 1)
    # Existing results stay replayable; nothing was evicted to make room.
    replay = await h.visitors.reserve_start(bootstrap, ids[0], "teacher")
    assert not replay.leader


# -- 7.2 caller disconnect and drain: see the real-app cases below ---------


@pytest.mark.parametrize("order", ["reset_first", "write_first"])
@pytest.mark.parametrize(
    "stage",
    [
        "student_chunk",
        "mentor_stream",
        "analysis_commit",
        "ledger_start",
        "ledger_finish",
    ],
)
def test_reset_during_student_mentor_analysis_and_shield_ledger_writes(
    stage, order, tmp_path
):
    """Barrier before student chunk, mentor stream, analysis commit and shield
    ledger write, both orders (28 reset race): the old DB is never recreated,
    the new DB sees 0 writes, and terminal frames carry the 11 codes
    (DEMO_STALE_GENERATION / cancelled) without body deltas.
    """
    run_case(
        "reset_race",
        tmp_path,
        RACE_STAGE=stage,
        RACE_ORDER=order,
        DEMO_REQUESTS_PER_MINUTE="600",
        DEMO_DRAIN_SECONDS="20",
        DEMO_CLEANUP_INTERVAL_SECONDS="300",
    )


# -- 7.2 / 9.1 real app (HTTP, DemoRequestDB) in a subprocess ---------------


# Short drain (7.2 timeout) and no background cleanup tick racing the cases.
SLOW_DRAIN = {
    "DEMO_DRAIN_SECONDS": "1",
    "DEMO_CLEANUP_INTERVAL_SECONDS": "300",
}


def run_case(case, tmp_path, **extra):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("DEMO_", "ANTHROPIC_", "OPENAI_", "AZURE_", "GOOGLE_", "GEMINI_")
        )
        and key
        not in {
            "APP_MODE",
            "DATABASE_URL",
            "TESTING",
            "SESSION_SECRET",
            "ENV",
            "BOOTSTRAP_ADMIN_ON_STARTUP",
            "PROVIDER_SECRET_ENCRYPTION_KEY",
            "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
        }
    }
    env.update(
        APP_MODE="demo",
        TESTING="false",
        ENV="production",
        DEMO_DATA_ROOT=str(tmp_path / "demo"),
        DEMO_SEED_DIR=str(tmp_path / "seed"),
        DEMO_ORIGIN="https://demo.test",
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET="s" * 48,
        DEMO_CSRF_SECRET="c" * 48,
        SESSION_SECRET="n" * 48,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        **extra,
    )
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reset_close_failure_is_db_locked_and_keeps_lease_and_file(tmp_path):
    run_case("close_failure", tmp_path, **SLOW_DRAIN)


def test_caller_disconnect_after_mark_still_completes_reset(tmp_path):
    run_case("disconnect", tmp_path, **SLOW_DRAIN)


def test_reset_alone_drains_and_separate_live_lease_times_out(tmp_path):
    run_case("live_lease", tmp_path, **SLOW_DRAIN)


def test_two_tabs_send_different_reset_ids_concurrently(tmp_path):
    run_case("two_tabs", tmp_path)


async def case_two_tabs(tmp):
    import test_demo_d1_04_app as app_tests

    from src import main

    app = main.app
    build_product_seed()
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        tab_a = app_tests.client_for(app)
        state = await app_tests.start(tab_a, "admin")
        tab_b = app_tests.client_for(app)
        tab_b.cookies.update(tab_a.cookies)  # one browser, two tabs
        make_context, arrived, gate = visitors.make_context, [], asyncio.Event()

        async def both_hold_ready_context(*args, **kwargs):
            ctx = await make_context(*args, **kwargs)
            arrived.append(ctx)
            if len(arrived) == 2:
                gate.set()
            await gate.wait()
            return ctx

        visitors.make_context = both_hold_ready_context
        ids = [str(uuid4()), str(uuid4())]
        try:
            responses = await asyncio.gather(
                *(
                    tab.post(
                        "/demo/reset",
                        json={
                            "request_id": rid,
                            "expected_generation": state["generation"],
                            "expected_auth_epoch": state["auth_epoch"],
                            "confirm": True,
                        },
                        headers=app_tests.headers(tab),
                    )
                    for tab, rid in zip((tab_a, tab_b), ids)
                )
            )
        finally:
            visitors.make_context = make_context
        winners = [r for r in responses if r.status_code in (200, 202)]
        losers = [r for r in responses if r.status_code == 409]
        assert len(winners) == len(losers) == 1, [r.text for r in responses]
        assert app_tests.code(losers[0]) == "DEMO_RESET_IN_PROGRESS", losers[
            0
        ].text
        assert not any("set-cookie" in r.headers for r in responses)
        winner_id = ids[responses.index(winners[0])]
        loser_id = ids[responses.index(losers[0])]
        final = await app_tests.wait_ready(tab_a, state["generation"] + 1)
        assert final["auth_epoch"] == state["auth_epoch"] + 1
        replay = await tab_b.get(
            "/demo/state", params={"request_id": winner_id}
        )
        assert replay.status_code == 200
        unknown = await tab_b.get(
            "/demo/state", params={"request_id": loser_id}
        )
        assert unknown.status_code == 404  # rejected id leaves no receipt


def build_product_seed():
    """The #197 product seed from its own CLI; main.demo_seed loads it."""
    built = subprocess.run(
        [sys.executable, "-m", "src.demo.seed", "build", "--out"]
        + [os.environ["DEMO_SEED_DIR"]],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr


def count_clones(visitors):
    clones, clone = [], visitors.root.clone

    async def counted(*args, **kwargs):
        clones.append(1)
        return await clone(*args, **kwargs)

    visitors.root.clone = counted
    return clones


async def post_reset(app_tests, client, rid=None, *, generation, epoch):
    return await client.post(
        "/demo/reset",
        json={
            "request_id": rid or str(uuid4()),
            "expected_generation": generation,
            "expected_auth_epoch": epoch,
            "confirm": True,
        },
        headers=app_tests.headers(client),
    )


async def case_close_failure(tmp):
    import test_demo_d1_04_app as app_tests
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    from src import main

    app = main.app
    build_product_seed()
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        client = app_tests.client_for(app)
        state = await app_tests.start(client, "admin")
        visitor = await app_tests.visitor_of(app, client)
        snap = await visitors.snapshot(visitor)
        close = AsyncSession.close

        async def failing_close(self):
            await close(self)  # no leak; the product must still keep the lease
            busy = sqlite3.OperationalError("database is locked")
            busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise OperationalError("close", {}, busy)

        # Persistent: the route guard retries the release after the reset fails.
        AsyncSession.close = failing_close
        try:
            response = await post_reset(
                app_tests,
                client,
                generation=state["generation"],
                epoch=state["auth_epoch"],
            )
        finally:
            AsyncSession.close = close
        assert response.status_code == 503, response.text
        assert app_tests.code(response) == "DEMO_DB_LOCKED"
        assert response.headers["retry-after"] == "1"
        assert visitors.lease_count((snap.visitor_id, 1)) == 1
        assert snap.ref.path.exists() and not snap.ref.factory.disposed
        assert (await visitors.snapshot(visitor)).state == "reset_failed"


async def case_disconnect(tmp):
    import test_demo_d1_04_app as app_tests

    from src import main

    app = main.app
    build_product_seed()
    async with app.router.lifespan_context(app):
        runtime = app.state.demo_request_db
        visitors, lifecycle = runtime.visitors, runtime.lifecycle
        clones = count_clones(visitors)
        client = app_tests.client_for(app)
        state = await app_tests.start(client, "admin")
        visitor = await app_tests.visitor_of(app, client)
        entered, proceed = asyncio.Event(), asyncio.Event()
        drain = lifecycle.drain

        async def gated(scope, timeout_seconds):
            entered.set()
            await proceed.wait()
            return await drain(scope, timeout_seconds)

        lifecycle.drain = gated
        try:
            rid = str(uuid4())
            caller = asyncio.create_task(
                post_reset(
                    app_tests,
                    client,
                    rid,
                    generation=state["generation"],
                    epoch=state["auth_epoch"],
                )
            )
            # Mark, handoff and own-lease release are done.
            await asyncio.wait_for(entered.wait(), 10)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert (await visitors.snapshot(visitor)).state == "resetting"
            progress = await client.get(
                "/demo/state", params={"request_id": rid}
            )
            assert progress.status_code == 202
        finally:
            proceed.set()
            del lifecycle.drain
        tasks = app.state.demo_session._tasks.values()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        await client.get("/demo/state")
        result = await client.get("/demo/state", params={"request_id": rid})
        assert result.status_code == 200
        assert result.json()["result"]["state"] == "ready"
        snap = await visitors.snapshot(visitor)
        assert (snap.state, snap.generation, snap.auth_epoch) == ("ready", 2, 2)
        assert len(clones) == 2  # one start + one reset clone


async def case_live_lease(tmp):
    import test_demo_d1_04_app as app_tests

    from src import main

    app = main.app
    build_product_seed()
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        client = app_tests.client_for(app)
        state = await app_tests.start(client, "admin")
        visitor = await app_tests.visitor_of(app, client)
        # Only the request's own lease exists: no timeout (7.2).
        alone = await post_reset(
            app_tests, client, generation=1, epoch=state["auth_epoch"]
        )
        assert alone.status_code == 200 and alone.json()["generation"] == 2
        state = (await client.get("/demo/state")).json()
        snap = await visitors.snapshot(visitor)
        lease = await visitors.acquire_lease(snap.ref, snap.auth_epoch)
        old_path = snap.ref.path
        began = time.monotonic()
        failed = await post_reset(
            app_tests, client, generation=2, epoch=state["auth_epoch"]
        )
        assert time.monotonic() - began >= 0.9
        assert failed.status_code == 503, failed.text
        assert app_tests.code(failed) == "DEMO_DRAIN_TIMEOUT"
        clones = count_clones(visitors)
        assert old_path.exists() and not snap.ref.factory.disposed
        state = (await client.get("/demo/state")).json()
        assert state["state"] == "reset_failed"
        assert state["generation"] == state["auth_epoch"] == 3
        await visitors.cleanup_retired()  # live lease: still not removable
        assert old_path.exists() and not snap.ref.factory.disposed
        await visitors.release_lease(lease)
        result = await visitors.cleanup_retired()
        assert not old_path.exists() and result.removed >= 1
        retry = await post_reset(app_tests, client, generation=3, epoch=3)
        assert retry.status_code == 200, retry.text
        assert retry.json()["generation"] == retry.json()["auth_epoch"] == 3
        assert len(clones) == 1


RACE_LEDGER = (
    "session",
    "message",
    "generation_run",
    "api_usage_log",
    "question_analysis",
    "session_summary",
    "session_feedback_report",
)
# stage -> (table, operation, statuses the parked write adds per order)
RACE_ADDS = {
    "student_chunk": ("generation_run", "student", ["running"], ["running"]),
    "mentor_stream": ("generation_run", "mentor", ["running"], ["completed"]),
    "analysis_commit": (
        "generation_run",
        "analysis",
        ["running"],
        ["ok"],  # analysis runs end ok/degraded, not completed
    ),
    "ledger_start": ("api_usage_log", "student", [], ["running"]),
    "ledger_finish": ("api_usage_log", "student", ["running"], ["completed"]),
}


def ledger_dump(path):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in RACE_LEDGER
        }


def run_statuses(path, table, operation):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return [
            status
            for (status,) in db.execute(
                f"SELECT status FROM {table} WHERE operation=? ORDER BY rowid",
                (operation,),
            )
        ]


class Gate:
    """One-shot barrier: the writer parks on go, arrived says it is there."""

    def __init__(self):
        self.arrived, self.go, self.used = asyncio.Event(), asyncio.Event(), 0

    async def park(self):
        self.arrived.set()
        await self.go.wait()


async def parked_or_done(gate, running):
    """Wait for the writer to park; a refusal or early end says why instead."""
    waiting = asyncio.create_task(gate.arrived.wait())
    await asyncio.wait(
        {waiting, running}, timeout=30, return_when=asyncio.FIRST_COMPLETED
    )
    if running.done() and running.result().status_code != 202:  # 202: analysis
        response = running.result()
        assert gate.arrived.is_set(), (response.status_code, response.text)
    await asyncio.wait_for(waiting, 60)


class Spy:
    """ASGI wrapper that logs each SSE frame the moment the server sends it."""

    def __init__(self, app, timeline, delta_sent):
        self.app, self.timeline, self.delta_sent = app, timeline, delta_sent

    async def __call__(self, scope, receive, send):
        async def spy(message):
            body = message.get("body", b"") if scope["type"] == "http" else b""
            for name in re.findall(rb"^event: (\S+)", body, re.M):
                self.timeline.append(name.decode())
                if name == b"output.delta":
                    self.delta_sent.set()
            await send(message)

        await self.app(scope, receive, spy)


def wrap_call(owner, name, gate, when):
    """Park the first call of owner.name before it ('before') or after it."""
    real = getattr(owner, name)

    async def wrapped(*args, **kwargs):
        first = not gate.used
        gate.used += 1
        if first and when == "before":
            await gate.park()
        result = await real(*args, **kwargs)
        if first and when == "after":
            await gate.park()
        return result

    setattr(owner, name, wrapped)
    return lambda: setattr(owner, name, real)


def wrap_chunk(gate, when):
    """Park the first student text chunk before it is emitted / after it."""
    from contextlib import aclosing

    from src.demo.provider import DemoProvider

    real = DemoProvider.stream_text

    async def wrapped(self, *args, **kwargs):
        async with aclosing(real(self, *args, **kwargs)) as events:
            async for event in events:
                first = event.type == "text_delta" and not gate.used
                gate.used += first
                if first and when == "before":
                    await gate.park()
                yield event
                if first and when == "after":
                    await gate.park()

    DemoProvider.stream_text = wrapped
    return lambda: setattr(DemoProvider, "stream_text", real)


def arm_race(stage, order, gate):
    """Install the barrier; reset_first parks just before the write, write_first
    right after it committed. Returns the list of undo callables."""
    from src.services import (
        analysis_pipeline,
        analysis_runs,
        call_execution,
        mentor_stream,
    )

    before = order == "reset_first"
    when = "before" if before else "after"
    if stage == "student_chunk":
        return [wrap_chunk(gate, when)]
    if stage == "analysis_commit":
        if not before:  # the whole task, once its commit is behind it
            return [wrap_call(analysis_runs, "execute_analysis", gate, when)]
        # The pipeline result is ready; the commit guard comes next.
        return [
            wrap_call(analysis_pipeline, "run_llm_pipeline", gate, "after"),
            wrap_call(analysis_runs, "chunk_executor", gate, "after"),
        ]
    owner, name = {
        "mentor_stream": (mentor_stream, "finish_mentor"),
        "ledger_start": (call_execution, "start_attempt"),
        "ledger_finish": (call_execution, "finish_attempt"),
    }[stage]
    return [wrap_call(owner, name, gate, when)]


def stretch_deadlines(factor=10):
    """Timeouts are not under test: CPU contention must not pre-empt a barrier."""
    from src.services import call_policy

    init = call_policy.CallDeadline.__init__

    def stretched(self, timeouts, role, **kwargs):
        timeouts = {key: value * factor for key, value in timeouts.items()}
        init(self, timeouts, role, **kwargs)

    call_policy.CallDeadline.__init__ = stretched


def sse_frames(response):
    assert response.status_code == 200, response.text
    return [
        (
            block.split("\n")[0].split(": ", 1)[1],
            json.loads(block.split("\n")[1].split(": ", 1)[1]),
        )
        for block in response.text.strip().split("\n\n")
        if block.startswith("event: ")
    ]


async def case_reset_race(tmp):
    import test_demo_d1_04_app as app_tests

    from src import main
    from src.demo.seed import INPUTS

    stage, order = os.environ["RACE_STAGE"], os.environ["RACE_ORDER"]
    table, operation, adds_first, adds_after = RACE_ADDS[stage]
    expected_adds = adds_first if order == "reset_first" else adds_after
    build_product_seed()
    app = main.app
    stretch_deadlines()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    timeline, delta_sent = [], asyncio.Event()
    async with app.router.lifespan_context(app):
        runtime = app.state.demo_request_db
        visitors, lifecycle = runtime.visitors, runtime.lifecycle
        client = app_tests.client_for(Spy(app, timeline, delta_sent))
        state = await app_tests.start(client, "teacher")
        ns = app_tests.namespace(state)
        visitor = await app_tests.visitor_of(app, client)
        snap = await visitors.snapshot(visitor)
        old_path, old_scope = snap.ref.path, snap.scope
        seed_path = app.state.demo_seed.path

        async def post(path, **kwargs):
            return await client.post(
                path, params=ns, headers=app_tests.headers(client), **kwargs
            )

        scenario = runtime.seed_refs["scenario"]
        created = await post("/sessions", json={"scenario_id": scenario})
        assert created.status_code == 201, created.text
        sid, turn = created.json()["id"], None

        async def student(text):
            return await post(
                f"/sessions/{sid}/turns/stream",
                json={"request_id": str(uuid4()), "content": text},
            )

        async def mentor():
            return await post(
                f"/sessions/{sid}/turns/{turn}/mentor/stream",
                json={"request_id": str(uuid4()), "trigger": "manual"},
            )

        async def end():
            return await post(
                f"/sessions/{sid}/end", json={"request_id": str(uuid4())}
            )

        # Unparked prefix: the turns the parked step builds on.
        target = {
            "student_chunk": student,
            "ledger_start": student,
            "ledger_finish": student,
            "mentor_stream": mentor,
            "analysis_commit": end,
        }[stage]
        if stage in ("mentor_stream", "analysis_commit"):
            for text in (INPUTS["A"], INPUTS["common"]):
                done = sse_frames(await student(text))
                assert done[-1][0] == "output.completed", done[-1]
                turn = done[0][1]["turn_id"]
        if stage == "analysis_commit":
            done = sse_frames(await mentor())
            assert done[-1][0] == "output.completed", done[-1]
        timeline.clear()
        pre = run_statuses(old_path, table, operation)

        entered, drain = asyncio.Event(), lifecycle.drain

        async def reset_marked(scope, timeout_seconds):
            # Mark, own-lease release and old-scope cancel are done (7.2).
            if not entered.is_set():
                timeline.append("RESET")
            entered.set()
            return await drain(scope, timeout_seconds)

        gate = Gate()
        undo = arm_race(stage, order, gate)
        lifecycle.drain = reset_marked
        request = student(INPUTS["A"]) if target is student else target()
        running = asyncio.create_task(request)
        try:
            await parked_or_done(gate, running)
            if stage == "student_chunk" and order == "write_first":
                await asyncio.wait_for(delta_sent.wait(), 30)  # on the wire
            parked = ledger_dump(old_path)
            assert run_statuses(old_path, table, operation) == (
                pre + expected_adds
            ), (stage, order)
            reset = asyncio.create_task(
                post_reset(
                    app_tests,
                    client,
                    generation=state["generation"],
                    epoch=state["auth_epoch"],
                )
            )
            await asyncio.wait_for(entered.wait(), 30)
            gate.go.set()
            reset_response = await asyncio.wait_for(reset, 60)
            result = await asyncio.wait_for(running, 60)
        finally:
            gate.go.set()
            del lifecycle.drain
            for restore in undo:
                restore()
        assert reset_response.status_code == 200, reset_response.text
        assert reset_response.json()["generation"] == state["generation"] + 1
        fresh = await app_tests.wait_ready(client, state["generation"] + 1)
        new_snap = await visitors.snapshot(visitor)
        new_path = new_snap.ref.path

        # Old DB: the parked write landed exactly when it came first, and
        # nothing was written after the reset point (no recreation either).
        assert ledger_dump(old_path) == parked
        # New DB: no write of the run; still the pristine seed rows.
        assert new_path != old_path
        assert ledger_dump(new_path) == ledger_dump(seed_path)

        # Terminal: body-free and coded, nothing but it after the reset point.
        if stage == "analysis_commit":
            assert result.status_code == 202, result.text
            run_id = result.json()["run_id"]
        else:
            frames = sse_frames(result)
            run_id = frames[0][1]["run_id"]
            kind, data = frames[-1]
            assert kind == "run.cancelled", frames[-1]
            assert data["status"] == "cancelled", data
            assert data["code"] == "DEMO_STALE_GENERATION", data
            assert data["retryable"] is False, data
            assert data["recovery"] == "demo.stale_generation", data
            assert "text" not in data and "turn_index" not in data, data
            assert {"generation", "auth_epoch", "browser_scope_id"} <= set(data)
            assert data["browser_scope_id"] == state["browser_scope_id"], data
            marker = timeline.index("RESET")
            assert timeline[-1] == "run.cancelled", timeline
            assert timeline.count("run.cancelled") == 1, timeline
            assert not [
                name for name in timeline[marker:] if name.startswith("output.")
            ], timeline
            if stage == "student_chunk":
                assert timeline[:marker].count("output.delta") == (
                    order == "write_first"
                ), timeline
        # The old namespace is refused; the new generation has no such run.
        stale = await client.get(f"/runs/{run_id}", params=ns)
        assert stale.status_code == 409, stale.text
        assert app_tests.code(stale) == "DEMO_STALE_GENERATION"
        gone = await client.get(
            f"/runs/{run_id}", params=app_tests.namespace(fresh)
        )
        assert gone.status_code == 404, gone.text

        # Cleanup drops the old file for good; only the new generation stays.
        cleaned = await visitors.cleanup_retired()
        assert cleaned.removed >= 1 and not old_path.exists()
        assert app_tests.visitor_files(root) == [new_path.name]
        assert visitors.lease_count(old_scope) == 0
        assert visitors.lease_count(new_snap.scope) == 0


if __name__ == "__main__":
    import test_demo_d1_04_app as app_tests

    async def run():
        try:
            await globals()["case_" + sys.argv[1]](Path(sys.argv[2]))
        finally:
            for client in app_tests.CLIENTS:
                await client.aclose()

    asyncio.run(run())

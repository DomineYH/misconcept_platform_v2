"""Demo control HTTP contract; security doubles do not prove middleware."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.routes.demo import (
    CookieValues,
    get_demo_security,
    get_demo_session,
    router,
)
from src.demo.session import (
    DemoError,
    DemoSession,
    StartReservation,
    StoredResult,
    public_state,
)


@pytest.mark.asyncio
async def test_unconfigured_router_fails_closed_without_cookies():
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://demo.test"
    ) as client:
        for path in ("/demo", "/demo/state"):
            response = await client.get(path)
            assert response.status_code == 503
            assert response.json()["detail"] == {
                "code": "DEMO_CONFIGURATION_INVALID",
                "retryable": False,
                "recovery": "demo.unavailable",
                "generation": None,
                "auth_epoch": None,
            }
            assert response.headers["cache-control"] == "no-store"
            assert "set-cookie" not in response.headers


async def test_entry_sets_only_contract_cookies_without_creating_visit():
    class Security:
        async def verified_bootstrap(self, request, *, issue):
            assert issue
            return "verified-reference"

        async def cookies(self, bootstrap):
            assert bootstrap == "verified-reference"
            return CookieValues("signed-bootstrap", "signed-current-csrf", True)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_demo_security] = Security
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://demo.test"
        ) as client:
            response = await client.get("/demo")
            assert response.status_code == 200
            assert 'value="teacher"' in response.text
            assert 'value="admin"' in response.text
            cookies = response.headers.get_list("set-cookie")
            assert len(cookies) == 2
            assert "demo_session_id=signed-bootstrap" in cookies[0]
            assert "Max-Age=28800" in cookies[0]
            assert "HttpOnly" in cookies[0]
            assert "demo_csrftoken=signed-current-csrf" in cookies[1]
            assert "Max-Age" not in cookies[1]
            assert "expires=" not in cookies[1].lower()
            assert "HttpOnly" not in cookies[1]
            for cookie in cookies:
                assert "Path=/" in cookie
                assert "SameSite=lax" in cookie
                assert "Secure" in cookie
                assert "Domain=" not in cookie
            assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.clear()


def uid():
    return str(uuid4())


class VisitorsDouble:
    """#191 state/admission/lock/receipt double, confined to this test file."""

    def __init__(self, factory):
        self.factory = factory
        self.lock = asyncio.Lock()
        self.records = {}
        self.by_op = {}
        self.clones = 0
        self.cancelled = 0
        self.clone_started = asyncio.Event()
        self.clone_continue = asyncio.Event()
        self.clone_continue.set()
        self.clone_error = None
        self.calls = []
        self.limit = 256
        self.admission_error = None

    async def lookup(self, bootstrap):
        if bootstrap not in self.records:
            raise DemoError("SESSION_REQUIRED")
        return bootstrap

    def record_for(self, visitor):
        if not isinstance(visitor, str):
            visitor = self.by_op[visitor.op_id]
        return self.records[visitor]

    @asynccontextmanager
    async def transition(self, visitor):
        async with self.lock:
            self.calls.append("lock")
            try:
                yield HandleDouble(self, self.record_for(visitor))
            finally:
                self.calls.append("unlock")

    async def reserve_start(self, bootstrap, request_id, role):
        async with self.lock:
            if self.admission_error:
                raise self.admission_error
            if bootstrap not in self.records:
                self.records[bootstrap] = SimpleNamespace(
                    browser_scope_id=f"scope-{bootstrap}",
                    state="preparing",
                    generation=1,
                    auth_epoch=1,
                    role=None,
                    expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
                    idle_ttl_seconds=1800,
                    absolute_ttl_seconds=7200,
                    current_ref=None,
                    operation=None,
                    last_error=None,
                    receipts={},
                    bodies={},
                    reserved_role=role,
                    attempt=0,
                    csrf_revision=1,
                    active_leases=0,
                )
            record = self.records[bootstrap]
            handle = HandleDouble(self, record)
            body = {"request_id": request_id, "role": role}
            stored = handle.replay(request_id, "start", body)
            if stored is not None:
                return StartReservation(record, replay=stored)
            handle.check_control_state("start")
            if record.state == "ready":
                record.bodies[request_id] = ("start", body)
                result = public_state(handle.snapshot()).model_copy(
                    update={"request_id": request_id}
                )
                record.receipts[request_id] = StoredResult(
                    "start",
                    request_id,
                    result,
                    record.browser_scope_id,
                    record.generation,
                    record.auth_epoch,
                )
                return StartReservation(record)
            record.bodies[request_id] = ("start", body)
            record.reserved_role = role
            op = self.operation(bootstrap, record, request_id, "start")
            record.state = "preparing"
            return StartReservation(record, op)

    def operation(self, bootstrap, record, request_id, operation):
        record.attempt += 1
        op = SimpleNamespace(
            op_id=uid(),
            visitor_id=bootstrap,
            generation=record.generation,
            attempt_no=record.attempt,
            operation=operation,
            request_id=request_id,
        )
        record.operation = op
        self.by_op[op.op_id] = bootstrap
        return op

    async def clone(self, op):
        assert not self.lock.locked()
        self.clones += 1
        self.clone_started.set()
        await self.clone_continue.wait()
        if self.clone_error:
            raise self.clone_error
        self.calls.append("clone")
        return SimpleNamespace(
            seed_refs=SimpleNamespace(
                admin=1,
                teacher=2,
                completed_session=1,
            )
        )

    async def cancel_preparation(self, op):
        assert not self.lock.locked()
        self.cancelled += 1
        self.calls.append("cancel_preparation")


class HandleDouble:
    def __init__(self, visitors, record):
        self.visitors = visitors
        self.visit = record

    def snapshot(self):
        record = self.visit
        if datetime.now(timezone.utc) >= record.expires_at:
            record.state = "expired"
        return SimpleNamespace(**vars(record))

    def error(self, code):
        return DemoError(
            code,
            generation=self.visit.generation,
            auth_epoch=self.visit.auth_epoch,
        )

    def replay(self, request_id, operation=None, body=None):
        record = self.visit
        if self.snapshot().state in ("expired", "closing"):
            raise self.error("EXPIRED")
        self.visitors.calls.append("replay")
        if request_id in record.bodies:
            if operation is not None and record.bodies[request_id] != (
                operation,
                body,
            ):
                raise self.error("REQUEST_CONFLICT")
            if request_id in record.receipts:
                return record.receipts[request_id]
            if record.operation and record.operation.request_id == request_id:
                return public_state(self.snapshot())
            return None
        if operation is not None and len(record.bodies) >= self.visitors.limit:
            raise self.error("LIMIT_REACHED")
        return None

    def check_control_state(self, operation):
        state = self.snapshot().state
        if state in ("expired", "closing"):
            raise self.error("EXPIRED")
        if state == "preparing" and self.visit.operation is not None:
            raise self.error(
                "START_IN_PROGRESS"
                if operation == "start"
                else "SESSION_REQUIRED"
            )
        if state == "resetting":
            raise self.error("RESET_IN_PROGRESS")
        if state == "prepare_failed" and operation != "start":
            raise self.error("PREPARE_FAILED")
        if state == "reset_failed" and operation != "reset":
            raise self.error("RESET_FAILED")

    def check(self, ctx):
        record = self.visit
        if self.snapshot().state in ("expired", "closing"):
            raise self.error("EXPIRED")
        if ctx.generation != record.generation:
            raise self.error("STALE_GENERATION")
        self.check_control_state("role")
        if ctx.auth_epoch != record.auth_epoch:
            raise self.error("STALE_ROLE")
        if getattr(ctx, "released", False):
            raise self.error("STALE_GENERATION")

    def record(self, operation, command, result, status):
        record = self.visit
        self.replay(command.request_id, operation, command.model_dump())
        record.bodies[command.request_id] = (operation, command.model_dump())
        if result is not None:
            record.receipts[command.request_id] = StoredResult(
                operation,
                command.request_id,
                result,
                record.browser_scope_id,
                record.generation,
                record.auth_epoch,
                status,
            )

    def change_role(self, ctx, role, busy):
        self.visitors.calls.append("change_role")
        self.check(ctx)
        if role != self.visit.role:
            if busy:
                raise self.error("BUSY")
            self.visit.role = role
            self.visit.auth_epoch += 1
            self.visit.csrf_revision += 1
        return self.snapshot()

    def mark_reset(self, ctx, request_id):
        record = self.visit
        if self.snapshot().state in ("expired", "closing"):
            raise self.error("EXPIRED")
        if ctx.generation != record.generation:
            raise self.error("STALE_GENERATION")
        self.check_control_state("reset")
        if ctx.auth_epoch != record.auth_epoch:
            raise self.error("STALE_ROLE")
        if record.state == "ready":
            self.check(ctx)
            record.retired_scope = (ctx.visitor_id, record.generation)
            record.generation += 1
            record.auth_epoch += 1
        record.state = "resetting"
        record.role = None
        record.current_ref = None
        op = self.visitors.operation(
            ctx.visitor_id, record, request_id, "reset"
        )
        self.visitors.calls.append("mark_reset")
        return op, record.retired_scope

    def publish(self, op, candidate):
        record = self.visit
        if self.snapshot().state in ("expired", "closing"):
            raise self.error("EXPIRED")
        if record.operation != op:
            raise self.error("STALE_GENERATION")
        record.current_ref = candidate
        record.state = "ready"
        record.role = (
            record.reserved_role if op.operation == "start" else "admin"
        )
        record.csrf_revision += 1
        record.last_error = None
        result = public_state(self.snapshot())
        record.receipts[op.request_id] = StoredResult(
            op.operation,
            op.request_id,
            result,
            record.browser_scope_id,
            record.generation,
            record.auth_epoch,
        )
        record.operation = None
        return self.snapshot()

    def fail(self, op, code, *, retry_after=None):
        record = self.visit
        if (
            self.snapshot().state in ("expired", "closing")
            or record.operation != op
        ):
            return False
        record.state = (
            "prepare_failed" if op.operation == "start" else "reset_failed"
        )
        record.current_ref = None
        record.role = None
        record.last_error = code
        error = self.error(code)
        record.receipts[op.request_id] = StoredResult(
            op.operation,
            op.request_id,
            error.envelope,
            record.browser_scope_id,
            record.generation,
            record.auth_epoch,
            error.status,
            error.retry_after if error.retry_after is not None else retry_after,
        )
        record.operation = None
        return True


class LifecycleDouble:
    def __init__(self, visitors):
        self.visitors = visitors
        self.busy = False
        self.remaining = 0
        self.scopes = []

    def busy_locked(self, handle, scope):
        assert self.visitors.lock.locked()
        self.visitors.calls.append("busy_locked")
        return self.busy

    async def cancel_scope(self, scope):
        assert not self.visitors.lock.locked()
        self.scopes.append(scope)
        self.visitors.calls.append("cancel_scope")
        return 0

    async def drain(self, scope, timeout_seconds):
        assert not self.visitors.lock.locked()
        assert self.visitors.records[scope[0]].active_leases == 0
        self.visitors.calls.append("drain")
        return SimpleNamespace(
            drained=self.remaining == 0, remaining=self.remaining
        )


class RequestDBDouble:
    """#194 handoff and close/release double; rollback uses a real session."""

    def __init__(self, visitors):
        self.visitors = visitors
        self.close_error = False

    def handoff_request_db(self, ctx):
        assert self.visitors.lock.locked()
        ctx.handoff = True
        self.visitors.calls.append("handoff")

    async def release_request_db(self, ctx):
        if getattr(ctx, "released", False):
            return
        assert not self.visitors.lock.locked()
        if getattr(ctx, "db", None) is not None:
            await ctx.db.rollback()
            self.visitors.calls.append("rollback")
            if self.close_error:
                raise DemoError("DB_LOCKED")
            await ctx.db.close()
            self.visitors.calls.append("close")
            self.visitors.records[ctx.visitor_id].active_leases -= 1
        ctx.released = True
        self.visitors.calls.append("release_lease")


class SecurityDouble:
    def __init__(self, visitors, factory):
        self.visitors = visitors
        self.factory = factory
        self.contexts = []
        self.verify_calls = 0

    async def verified_bootstrap(self, request, *, issue):
        value = request.cookies.get("demo_session_id")
        if value is None and issue:
            return "A"
        if value not in ("A", "B"):
            raise DemoError("SESSION_REQUIRED")
        return value

    async def cookies(self, bootstrap):
        record = self.visitors.records.get(bootstrap)
        revision = record.csrf_revision if record else 1
        return CookieValues(bootstrap, f"{bootstrap}-{revision}", True)

    async def verify_csrf(self, request, bootstrap):
        self.verify_calls += 1
        record = self.visitors.records.get(bootstrap)
        revision = record.csrf_revision if record else 1
        token = f"{bootstrap}-{revision}"
        origin = request.headers.get("origin")
        referer = request.headers.get("referer", "")
        if (
            request.cookies.get("demo_csrftoken") != token
            or request.headers.get("x-csrf-token") != token
            or not (
                origin == "https://demo.test"
                or (origin is None and referer.startswith("https://demo.test/"))
            )
        ):
            raise DemoError("CSRF_INVALID")

    async def context(
        self, request, bootstrap, expected_generation, expected_auth_epoch
    ):
        record = self.visitors.records[bootstrap]
        # A context captures client expectations; the handle remains authority.
        HandleDouble(self.visitors, record).check_control_state(
            "reset" if request.url.path.endswith("reset") else "role"
        )
        ctx = SimpleNamespace(
            verified_visitor=bootstrap,
            visitor_id=bootstrap,
            browser_scope_id=record.browser_scope_id,
            generation=expected_generation or record.generation,
            auth_epoch=expected_auth_epoch or record.auth_epoch,
            role=record.role,
            user_id=1 if record.role == "admin" else 2,
            factory=self.factory,
            lease=object(),
            released=False,
            handoff=False,
            db=None,
        )
        if record.state == "ready":
            ctx.db = self.factory()
            await ctx.db.execute(text("SELECT 1"))
            record.active_leases += 1
        self.contexts.append(ctx)
        return ctx


@pytest.fixture
async def harness(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            'CREATE TABLE "user" (id INTEGER PRIMARY KEY, role TEXT)'
        )
        await conn.exec_driver_sql(
            "INSERT INTO \"user\" VALUES (1, 'admin'), (2, 'teacher')"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    visitors = VisitorsDouble(factory)
    lifecycle = LifecycleDouble(visitors)
    request_db = RequestDBDouble(visitors)
    session = DemoSession(visitors, lifecycle, request_db)
    security = SecurityDouble(visitors, factory)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_demo_security] = lambda: security
    app.dependency_overrides[get_demo_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://demo.test"
    ) as client:
        item = SimpleNamespace(
            app=app,
            client=client,
            visitors=visitors,
            lifecycle=lifecycle,
            request_db=request_db,
            session=session,
            security=security,
            factory=factory,
            engine=engine,
        )
        try:
            await client.get("/demo")
            yield item
        finally:
            await session.shutdown()
            for ctx in security.contexts:
                request_db.close_error = False
                await request_db.release_request_db(ctx)
            app.dependency_overrides.clear()
            await engine.dispose()


def headers(client):
    return {
        "x-csrf-token": client.cookies.get("demo_csrftoken"),
        "origin": "https://demo.test",
    }


async def start(h, role="teacher", request_id=None):
    return await h.client.post(
        "/demo/start",
        json={
            "request_id": request_id or uid(),
            "role": role,
        },
        headers=headers(h.client),
    )


async def test_start_schema_replay_and_current_cookie_sync(harness):
    h = harness
    request_id = uid()
    response = await start(h, request_id=request_id)
    assert response.status_code == 201
    state = response.json()
    assert set(state) == {
        "contract_version",
        "content_version",
        "fixture_version",
        "state",
        "browser_scope_id",
        "generation",
        "auth_epoch",
        "role",
        "expires_at",
        "idle_ttl_seconds",
        "absolute_ttl_seconds",
        "request_id",
        "csrf_cookie_name",
        "csrf_header_name",
        "next_url",
        "active",
        "provenance",
        "last_error",
        "tour_facts",
    }
    assert state["role"] == "teacher"
    assert state["generation"] == state["auth_epoch"] == 1
    assert state["tour_facts"] is None
    assert state["next_url"] == "/scenarios?ds=scope-A&dg=1&da=1"
    assert state["provenance"]["external_ai_called"] is False
    assert "set-cookie" not in response.headers
    assert not any(
        word in response.text
        for word in ("factory", "visitor_id", "user_id", "sqlite", "password")
    )
    # Publication rotates CSRF; replay does not bypass its verification.
    denied = await start(h, request_id=request_id)
    assert denied.status_code == 403
    await h.client.get("/demo/state")
    replay = await start(h, request_id=request_id)
    assert replay.status_code == 200
    assert replay.json()["historical"] is False
    assert replay.json()["result"]["role"] == "teacher"
    assert "set-cookie" not in replay.headers
    existing = await start(h, "admin")
    assert existing.status_code == 200
    assert existing.json()["role"] == "teacher"
    assert h.visitors.clones == 1


@pytest.mark.parametrize("form", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        {"role": "student"},
        {"request_id": "NOT-A-UUID"},
        {"request_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"user_id": 1},
        {"visitor_id": "A"},
        {"db_url": "sqlite:///secret"},
        {"path": "/tmp/db"},
        {"return_url": "https://evil.test"},
        {"extra": "secret"},
    ],
)
async def test_invalid_start_json_and_form_has_no_side_effects(
    harness, form, body
):
    h = harness
    payload = {"request_id": uid(), "role": "teacher", **body}
    response = await h.client.post(
        "/demo/start",
        headers=headers(h.client),
        **({"data": payload} if form else {"json": payload}),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DEMO_INVALID_REQUEST"
    assert "secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert h.visitors.clones == 0
    assert h.visitors.records == {}


async def test_start_barrier_and_in_progress_replay(harness):
    h = harness
    h.visitors.clone_continue.clear()
    request_id = uid()
    leader = asyncio.create_task(start(h, "teacher", request_id))
    await h.visitors.clone_started.wait()
    replay = await start(h, "teacher", request_id)
    assert replay.status_code == 202
    assert replay.json()["role"] is None
    assert replay.json()["next_url"] is None
    assert replay.json()["request_id"] == request_id
    loser = await start(h, "admin")
    assert loser.status_code == 409
    assert loser.json()["detail"]["code"] == "DEMO_START_IN_PROGRESS"
    conflict = await start(h, "admin", request_id)
    assert conflict.json()["detail"]["code"] == "DEMO_REQUEST_CONFLICT"
    for response in (replay, loser, conflict):
        assert "set-cookie" not in response.headers
    h.visitors.clone_continue.set()
    assert (await leader).status_code == 201
    assert h.visitors.clones == len(h.visitors.records) == 1
    assert h.visitors.records["A"].role == "teacher"


@pytest.mark.parametrize(
    "code,status",
    [
        ("PREPARE_FAILED", 503),
        ("STORAGE_LIMIT", 507),
        ("DB_LOCKED", 503),
        ("VERSION_MISMATCH", 503),
        (None, 503),
    ],
)
async def test_prepare_failure_replay_then_new_attempt(harness, code, status):
    h = harness
    h.visitors.clone_error = (
        DemoError(code) if code else RuntimeError("SQL secret")
    )
    request_id = uid()
    response = await start(h, request_id=request_id)
    assert response.status_code == status
    assert "secret" not in response.text
    state = await h.client.get("/demo/state")
    assert state.status_code == 200
    assert state.json()["state"] == "prepare_failed"
    assert state.json()["role"] is state.json()["next_url"] is None
    deadline = state.json()["expires_at"]
    replay = await start(h, request_id=request_id)
    assert replay.status_code == status
    assert (
        replay.json()["result"]["detail"]["code"]
        == f"DEMO_{code or 'PREPARE_FAILED'}"
    )
    assert h.visitors.clones == 1
    h.visitors.clone_error = None
    retry = await start(h)
    assert retry.status_code == 201
    assert retry.json()["generation"] == retry.json()["auth_epoch"] == 1
    assert retry.json()["expires_at"] == deadline
    assert h.visitors.records["A"].attempt == 2


async def test_role_busy_noop_stale_and_deleted_teacher(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    h.lifecycle.busy = True
    command = {
        "request_id": uid(),
        "role": "admin",
        "expected_generation": 1,
        "expected_auth_epoch": 1,
    }
    response = await h.client.post(
        "/demo/role", json=command, headers=headers(h.client)
    )
    assert response.status_code == 200
    assert response.json()["auth_epoch"] == 1
    response = await h.client.post(
        "/demo/role",
        json={**command, "request_id": uid(), "role": "teacher"},
        headers=headers(h.client),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DEMO_BUSY"
    h.lifecycle.busy = False
    response = await h.client.post(
        "/demo/role",
        data={**command, "request_id": uid(), "role": "teacher"},
        headers=headers(h.client),
    )
    assert response.status_code == 200
    assert response.json()["auth_epoch"] == 2
    assert (
        h.visitors.calls[-3:-1] == ["change_role", "replay"]
        or "change_role" in h.visitors.calls
    )
    await h.client.get("/demo/state")
    response = await h.client.post(
        "/demo/role",
        json={**command, "request_id": uid(), "role": "teacher"},
        headers=headers(h.client),
    )
    assert response.json()["detail"]["code"] == "DEMO_STALE_ROLE"
    response = await h.client.post(
        "/demo/role",
        json={**command, "request_id": uid(), "expected_auth_epoch": 2},
        headers=headers(h.client),
    )
    assert response.status_code == 200
    await h.client.get("/demo/state")
    async with h.factory() as db:
        await db.execute(text('DELETE FROM "user" WHERE id=2'))
        await db.commit()
    response = await h.client.post(
        "/demo/role",
        json={
            **command,
            "request_id": uid(),
            "expected_auth_epoch": 3,
            "role": "teacher",
        },
        headers=headers(h.client),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DEMO_ROLE_DELETED"


async def reset(h, request_id=None, generation=1, epoch=1, **extra):
    return await h.client.post(
        "/demo/reset",
        json={
            "request_id": request_id or uid(),
            "expected_generation": generation,
            "expected_auth_epoch": epoch,
            "confirm": True,
            **extra,
        },
        headers=headers(h.client),
    )


async def test_reset_handoff_order_and_replay(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    h.visitors.calls.clear()
    request_id = uid()
    response = await reset(h, request_id)
    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    assert response.json()["role"] == "admin"
    assert response.json()["generation"] == response.json()["auth_epoch"] == 2
    assert "set-cookie" not in response.headers
    calls = h.visitors.calls
    mark = calls.index("mark_reset")
    assert calls[mark : mark + 3] == ["mark_reset", "handoff", "unlock"]
    assert (
        calls.index("rollback")
        < calls.index("close")
        < calls.index("release_lease")
    )
    assert (
        calls.index("release_lease")
        < calls.index("cancel_scope")
        < calls.index("drain")
        < calls.index("clone")
    )
    assert h.lifecycle.scopes == [("A", 1)]
    await h.client.get("/demo/state")
    # Replay is looked up before this stale g/epoch could resolve a DB context.
    contexts = len(h.security.contexts)
    response = await reset(h, request_id)
    assert response.status_code == 200
    assert response.json()["result"]["generation"] == 2
    assert len(h.security.contexts) == contexts
    assert h.visitors.records["A"].generation == 2
    assert h.visitors.clones == 2


async def test_reset_disconnect_keeps_task_and_in_progress_receipt(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    h.visitors.clone_started.clear()
    h.visitors.clone_continue.clear()
    request_id = uid()
    caller = asyncio.create_task(reset(h, request_id))
    await h.visitors.clone_started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert len(h.session._tasks) == 1
    progress = await reset(h, request_id)
    assert progress.status_code == 202
    assert progress.json()["state"] == "resetting"
    assert progress.json()["generation"] == 2
    observed = await h.client.get(f"/demo/state?request_id={request_id}")
    assert observed.status_code == 202
    assert "set-cookie" not in observed.headers
    other = await reset(h)
    assert other.json()["detail"]["code"] == "DEMO_RESET_IN_PROGRESS"
    h.visitors.clone_continue.set()
    await asyncio.gather(*h.session._tasks.values())
    await h.client.get("/demo/state")
    result = await h.client.get(f"/demo/state?request_id={request_id}")
    assert result.status_code == 200
    assert result.json()["result"]["state"] == "ready"
    assert h.visitors.records["A"].generation == 2


async def test_live_other_lease_times_out_without_clone_then_retries_reserved_generation(
    harness,
):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    h.lifecycle.remaining = 1
    request_id = uid()
    response = await reset(h, request_id)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DEMO_DRAIN_TIMEOUT"
    assert h.visitors.clones == 1
    state = await h.client.get("/demo/state")
    assert state.json()["state"] == "reset_failed"
    assert state.json()["generation"] == state.json()["auth_epoch"] == 2
    replay = await reset(h, request_id)
    assert replay.status_code == 503
    assert replay.json()["result"]["detail"]["code"] == "DEMO_DRAIN_TIMEOUT"
    assert h.visitors.clones == 1
    h.lifecycle.remaining = 0
    stale = await reset(h)
    assert stale.json()["detail"]["code"] == "DEMO_STALE_GENERATION"
    retry = await reset(h, generation=2, epoch=2)
    assert retry.status_code == 200
    assert retry.json()["generation"] == retry.json()["auth_epoch"] == 2
    assert h.visitors.records["A"].attempt == 3


async def test_reset_close_failure_preserves_lease_and_fails_closed(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    h.request_db.close_error = True
    h.visitors.calls.clear()
    response = await reset(h)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DEMO_DB_LOCKED"
    assert response.headers["retry-after"] == "1"
    assert "release_lease" not in h.visitors.calls
    assert "cancel_scope" not in h.visitors.calls
    assert h.visitors.records["A"].active_leases == 1
    assert h.visitors.clones == 1


async def test_reset_shutdown_cancels_builder_and_records_failure(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    h.visitors.clone_started.clear()
    h.visitors.clone_continue.clear()
    request_id = uid()
    caller = asyncio.create_task(reset(h, request_id))
    await h.visitors.clone_started.wait()
    await h.session.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert h.visitors.cancelled == 1
    result = await h.client.get(f"/demo/state?request_id={request_id}")
    assert result.status_code == 503
    assert result.json()["result"]["detail"]["code"] == "DEMO_RESET_FAILED"
    assert not h.session._tasks


@pytest.mark.parametrize(
    "code,status,retryable,recovery,retry_after",
    [
        ("SESSION_REQUIRED", 401, False, "start_required", None),
        ("EXPIRED", 410, False, "expired", None),
        ("STALE_GENERATION", 409, False, "stale_generation", None),
        ("STALE_ROLE", 409, False, "stale_role", None),
        ("START_IN_PROGRESS", 409, False, "preparing", None),
        ("PREPARE_FAILED", 503, True, "prepare_failed", None),
        ("RESET_IN_PROGRESS", 409, False, "resetting", None),
        ("RESET_FAILED", 503, True, "reset_failed", None),
        ("DRAIN_TIMEOUT", 503, True, "reset_failed", None),
        ("DB_LOCKED", 503, True, "retry_later", 1),
        ("ROLE_DELETED", 409, False, "role_deleted", None),
        ("BUSY", 409, True, "wait_or_stop", None),
        ("CSRF_INVALID", 403, False, "csrf_refresh", None),
        ("LIMIT_REACHED", 429, False, "limit_reached", None),
        ("RATE_LIMITED", 429, True, "retry_later", 17),
        ("CAPACITY", 503, True, "capacity", 30),
        ("STORAGE_LIMIT", 507, False, "storage_limit", None),
        ("CLEANUP_BACKLOG", 503, True, "retry_later", 30),
        ("VERSION_MISMATCH", 503, False, "unavailable", None),
        ("CONFIGURATION_INVALID", 503, False, "unavailable", None),
        ("INVALID_REQUEST", 422, False, "check_input", None),
        ("REQUEST_CONFLICT", 409, False, "refresh_state", None),
        ("RESOURCE_NOT_FOUND", 404, False, "not_found", None),
    ],
)
async def test_dependency_error_table_is_preserved(
    harness, code, status, retryable, recovery, retry_after
):
    h = harness
    h.visitors.admission_error = DemoError(code, retry_after=retry_after)
    response = await start(h)
    assert response.status_code == status
    assert response.json() == {
        "detail": {
            "code": f"DEMO_{code}",
            "retryable": retryable,
            "recovery": f"demo.{recovery}",
            "generation": None,
            "auth_epoch": None,
        }
    }
    assert response.headers.get("retry-after") == (
        str(retry_after) if retry_after else None
    )
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert h.visitors.clones == 0


@pytest.mark.parametrize("path", ["role", "reset"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_generation", True),
        ("expected_generation", 0),
        ("expected_generation", -1),
        ("expected_generation", "1"),
        ("expected_auth_epoch", False),
        ("expected_auth_epoch", 0),
        ("expected_auth_epoch", -1),
        ("expected_auth_epoch", 1.1),
    ],
)
async def test_strict_epochs_reject_before_context(harness, path, field, value):
    h = harness
    payload = {
        "request_id": uid(),
        "expected_generation": 1,
        "expected_auth_epoch": 1,
        **({"role": "admin"} if path == "role" else {"confirm": True}),
        field: value,
    }
    response = await h.client.post(
        f"/demo/{path}", json=payload, headers=headers(h.client)
    )
    assert response.status_code == 422
    assert not h.security.contexts
    assert not h.visitors.records


async def test_signature_csrf_and_origin_precede_schema_and_replay(harness):
    h = harness
    h.client.cookies.clear()
    response = await h.client.post(
        "/demo/start",
        content="bad json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401
    assert h.security.verify_calls == 0
    await h.client.get("/demo")
    response = await h.client.post(
        "/demo/start",
        content="bad json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 403
    assert not h.visitors.calls
    response = await h.client.post(
        "/demo/start",
        json={"request_id": uid(), "role": "teacher"},
        headers={**headers(h.client), "origin": "https://evil.test"},
    )
    assert response.status_code == 403
    response = await h.client.post(
        "/demo/start",
        data={"request_id": uid(), "role": "teacher"},
        headers={
            "x-csrf-token": h.client.cookies.get("demo_csrftoken"),
            "referer": "https://demo.test/demo",
        },
    )
    assert response.status_code == 201


async def test_malformed_multipart_is_safe_422(harness):
    response = await harness.client.post(
        "/demo/start",
        content="not multipart",
        headers={
            **headers(harness.client),
            "content-type": "multipart/form-data",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DEMO_INVALID_REQUEST"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


async def test_historical_reset_receipt_cannot_restore_old_mapping(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    x = uid()
    assert (await reset(h, x)).status_code == 200
    await h.client.get("/demo/state")
    assert (await reset(h, generation=2, epoch=2)).status_code == 200
    await h.client.get("/demo/state")
    response = await h.client.get(f"/demo/state?request_id={x}")
    assert response.status_code == 200
    assert response.json()["historical"] is True
    assert response.json()["result"]["next_url"] is None
    assert response.json()["current"]["generation"] == 3
    assert "set-cookie" not in response.headers
    assert h.visitors.records["A"].generation == 3


class TourContentDouble:
    """Verified #195/#197 hash/content boundary; facts themselves are real SQL."""

    first_inputs = frozenset({"A", "B", "C"})
    common_input = "common"

    def supported_snapshot(self, row):
        return (
            row["config_hash"] == "supported"
            and row["snapshot_origin"] == "native"
        )


async def tour_tables(h):
    async with h.engine.begin() as conn:
        for ddl in (
            'CREATE TABLE "session" (id INTEGER PRIMARY KEY, deleted_at TEXT, ended_at TEXT, config_snapshot_json TEXT, config_hash TEXT, snapshot_origin TEXT, source_scenario_version INTEGER, snapshot_created_at TEXT)',
            "CREATE TABLE message (id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT, turn_id TEXT, turn_index INTEGER, generation_run_id TEXT)",
            "CREATE TABLE generation_run (id TEXT PRIMARY KEY, session_id INTEGER, turn_id TEXT, operation TEXT, status TEXT, result_kind TEXT, mentor_trigger TEXT, accepted_report_id INTEGER, accepted_report_version INTEGER)",
            "CREATE TABLE session_summary (id INTEGER PRIMARY KEY, session_id INTEGER)",
            "CREATE TABLE session_feedback_report (id INTEGER PRIMARY KEY, session_id INTEGER, version INTEGER, status TEXT, payload_json TEXT)",
        ):
            await conn.exec_driver_sql(ddl)
    h.visitors.records["A"].current_ref.tour_content = TourContentDouble()


async def tour_session(h, sid, *, supported=True, deleted=False, ended=False):
    async with h.factory() as db:
        await db.execute(
            text(
                "INSERT INTO \"session\" VALUES (:id, :deleted, :ended, :snapshot, :hash, 'native', 1, '2026-01-01')"
            ),
            {
                "id": sid,
                "deleted": "2026-01-02" if deleted else None,
                "ended": "2026-01-02" if ended else None,
                "snapshot": '{"config_schema_version":1}',
                "hash": "supported" if supported else "unsupported",
            },
        )
        await db.commit()


async def tour_turn(
    h, sid, index, teacher, *, complete=True, coaching="message", trigger="auto"
):
    turn = uid()
    student_run, mentor_run = uid(), uid()
    async with h.factory() as db:
        for role, content, run in (
            ("teacher", teacher, None),
            ("student", "prepared reply", student_run),
        ):
            await db.execute(
                text(
                    "INSERT INTO message (session_id, role, content, turn_id, turn_index, generation_run_id) VALUES (:sid,:role,:content,:turn,:idx,:run)"
                ),
                {
                    "sid": sid,
                    "role": role,
                    "content": content,
                    "turn": turn,
                    "idx": index,
                    "run": run,
                },
            )
        await db.execute(
            text(
                "INSERT INTO generation_run (id,session_id,turn_id,operation,status,result_kind) VALUES (:run,:sid,:turn,'student',:status,'message')"
            ),
            {
                "run": student_run,
                "sid": sid,
                "turn": turn,
                "status": "completed" if complete else "failed",
            },
        )
        await db.execute(
            text(
                "INSERT INTO generation_run (id,session_id,turn_id,operation,status,result_kind,mentor_trigger) VALUES (:run,:sid,:turn,'mentor','completed',:kind,:trigger)"
            ),
            {
                "run": mentor_run,
                "sid": sid,
                "turn": turn,
                "kind": coaching,
                "trigger": trigger,
            },
        )
        if coaching == "message":
            await db.execute(
                text(
                    "INSERT INTO message (session_id,role,content,turn_id,turn_index,generation_run_id) VALUES (:sid,'tutor','prepared coaching',:turn,:idx,:run)"
                ),
                {
                    "sid": sid,
                    "turn": turn,
                    "idx": index,
                    "run": mentor_run,
                },
            )
        await db.commit()


async def tour_analysis(h, sid, status="ok", adopted=True):
    run_id = uid()
    async with h.factory() as db:
        await db.execute(
            text("INSERT INTO session_summary (session_id) VALUES (:sid)"),
            {"sid": sid},
        )
        await db.execute(
            text(
                "INSERT INTO session_feedback_report (id,session_id,version,status,payload_json) VALUES (:id,:sid,2,:status,:payload)"
            ),
            {
                "id": sid,
                "sid": sid,
                "status": status,
                "payload": '{"metadata":{"run_id":"'
                + run_id
                + '","outcome":"dialogue"}}',
            },
        )
        await db.execute(
            text(
                "INSERT INTO generation_run (id,session_id,turn_id,operation,status,accepted_report_id,accepted_report_version) VALUES (:run,:sid,:turn,'analysis',:status,:report,2)"
            ),
            {
                "run": run_id,
                "sid": sid,
                "turn": uid(),
                "status": status,
                "report": sid if adopted else None,
            },
        )
        await db.commit()


async def facts(h):
    response = await h.client.get("/demo/state?include_tour=true")
    assert response.status_code == 200
    return response.json()["tour_facts"]


async def test_tour_schema_seed_exclusion_and_pinned_first_session(harness):
    h = harness
    await start(h)
    await tour_tables(h)
    initial = await facts(h)
    assert initial == {
        "schema_version": 1,
        "browser_scope_id": "scope-A",
        "generation": 1,
        "auth_epoch": 1,
        "journey_session_id": None,
        "teacher_entered": True,
        "scenario_created": False,
        "dialogue_complete": False,
        "analysis_ready": False,
        "teacher_deleted": False,
        "session_deleted": False,
    }
    await tour_session(h, 1, ended=True)  # Seed must never become the journey.
    await tour_session(h, 2)
    await tour_session(h, 3, ended=True)
    await tour_turn(h, 2, 1, "A")
    await tour_turn(h, 3, 1, "B")
    await tour_turn(h, 3, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 3)
    current = await facts(h)
    assert current["journey_session_id"] == 2
    assert current["scenario_created"] is True
    assert current["dialogue_complete"] is current["analysis_ready"] is False
    await tour_turn(h, 2, 2, "unsupported")
    await tour_turn(h, 2, 3, " common ", coaching="no_intervention")
    current = await facts(h)
    assert current["dialogue_complete"] is True
    assert current["analysis_ready"] is False
    async with h.factory() as db:
        await db.execute(
            text("UPDATE \"session\" SET ended_at='2026-01-02' WHERE id=2")
        )
        await db.commit()
    await tour_analysis(h, 2)
    current = await facts(h)
    assert current["analysis_ready"] is True
    assert current["journey_session_id"] == 2
    plain = await h.client.get("/demo/state")
    assert plain.json()["tour_facts"] is None


@pytest.mark.parametrize(
    "complete,coaching,trigger,status,adopted",
    [
        (False, "message", "auto", "ok", True),
        (True, "message", "manual", "ok", True),
        (True, "no_intervention", "auto", "ok", True),
        (True, "message", "auto", "degraded", True),
        (True, "message", "auto", "ok", False),
    ],
)
async def test_tour_requires_completed_supported_turns_auto_coaching_and_adopted_analysis(
    harness, complete, coaching, trigger, status, adopted
):
    h = harness
    await start(h)
    await tour_tables(h)
    await tour_session(h, 2, ended=True)
    await tour_turn(
        h, 2, 1, "A", complete=complete, coaching=coaching, trigger=trigger
    )
    await tour_turn(h, 2, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 2, status, adopted)
    current = await facts(h)
    assert current["analysis_ready"] is False
    assert current["dialogue_complete"] is (
        complete and coaching == "message" and trigger == "auto"
    )


async def test_tour_teacher_history_survives_role_changes_and_cache_clears_on_reset(
    harness,
):
    h = harness
    await start(h, "admin")
    await tour_tables(h)
    assert (await facts(h))["teacher_entered"] is False
    response = await h.client.post(
        "/demo/role",
        json={
            "request_id": uid(),
            "role": "teacher",
            "expected_generation": 1,
            "expected_auth_epoch": 1,
        },
        headers=headers(h.client),
    )
    assert response.status_code == 200
    assert response.json()["tour_facts"] is None
    assert (await facts(h))["teacher_entered"] is True
    response = await h.client.post(
        "/demo/role",
        json={
            "request_id": uid(),
            "role": "admin",
            "expected_generation": 1,
            "expected_auth_epoch": 2,
        },
        headers=headers(h.client),
    )
    assert response.status_code == 200
    current = await facts(h)
    assert current["teacher_entered"] is True
    assert current["auth_epoch"] == 3
    response = await reset(h, epoch=3)
    assert response.status_code == 200
    h.visitors.records["A"].current_ref.tour_content = TourContentDouble()
    current = await facts(h)
    assert current["generation"] == 2
    assert current["teacher_entered"] is False


async def test_tour_capture_retain_deletion_and_rollback_without_body_reads(
    harness,
):
    from sqlalchemy import event

    from src.demo.session import capture_tour_facts, retain_tour_facts

    h = harness
    await start(h)
    await tour_tables(h)
    await tour_session(h, 2, ended=True)
    await tour_turn(h, 2, 1, "B")
    await tour_turn(h, 2, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 2)
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/state"))
    ctx = await h.security.context(request, "A", 1, 1)
    try:
        async with h.visitors.transition("A") as handle:
            projection = await capture_tour_facts(ctx, handle)
            assert projection.analysis_ready
            # Capture itself retains nothing, including on a rollback path.
            async with h.factory() as db:
                await db.execute(
                    text(
                        "UPDATE \"session\" SET deleted_at='2026-01-02' WHERE id=2"
                    )
                )
                await db.rollback()
            async with h.factory() as db:
                await db.execute(
                    text(
                        "UPDATE \"session\" SET deleted_at='2026-01-02' WHERE id=2"
                    )
                )
                await db.commit()
            tombstone = await capture_tour_facts(ctx, handle)
            assert tombstone.journey_session_id == 2
            assert tombstone.scenario_created is False
            assert tombstone.dialogue_complete is False
            assert tombstone.analysis_ready is False
            # #200 retains its pre-delete proof only after successful commit.
            retain_tour_facts(
                handle, projection.model_copy(update={"session_deleted": True})
            )
    finally:
        await h.request_db.release_request_db(ctx)
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
    try:
        current = await facts(h)
        assert current["journey_session_id"] == 2
        assert current["analysis_ready"] is current["dialogue_complete"] is True
        assert current["session_deleted"] is True
        assert not any(
            "from message" in s
            or "from generation_run" in s
            or "session_feedback_report" in s
            for s in statements
        )
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
    async with h.factory() as db:
        await db.execute(text('DELETE FROM "user" WHERE id=2'))
        await db.commit()
    current = await facts(h)
    assert current["teacher_deleted"] is True
    assert current["analysis_ready"] is True
    assert current["journey_session_id"] == 2


async def test_tour_first_tombstone_does_not_invent_historical_progress(
    harness,
):
    h = harness
    await start(h, "admin")
    await tour_tables(h)
    await tour_session(h, 1, ended=True)
    await tour_session(h, 2, deleted=True, ended=True)
    await tour_session(h, 3, ended=True)
    await tour_turn(h, 2, 1, "A")
    await tour_turn(h, 2, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 2)
    current = await facts(h)
    assert current["journey_session_id"] == 2
    assert current["session_deleted"] is True
    for name in (
        "teacher_entered",
        "scenario_created",
        "dialogue_complete",
        "analysis_ready",
    ):
        assert current[name] is False


async def test_tour_unsupported_snapshot_and_input_never_advance(harness):
    h = harness
    await start(h)
    await tour_tables(h)
    await tour_session(h, 2, supported=False, ended=True)
    await tour_session(h, 3, ended=True)
    await tour_turn(h, 2, 1, "A")
    await tour_turn(h, 2, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 2)
    await tour_turn(h, 3, 1, "not A")
    await tour_turn(h, 3, 2, "common", coaching="no_intervention")
    await tour_analysis(h, 3)
    current = await facts(h)
    assert current["journey_session_id"] == 3
    assert current["dialogue_complete"] is current["analysis_ready"] is False


async def test_expired_and_257th_new_id_keep_old_receipts_and_cookie_policy(
    harness,
):
    h = harness
    first = uid()
    await start(h, request_id=first)
    await h.client.get("/demo/state")
    for _ in range(255):
        assert (await start(h)).status_code == 200
    response = await start(h)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "DEMO_LIMIT_REACHED"
    assert "set-cookie" not in response.headers
    assert h.visitors.clones == 1
    replay = await h.client.get(f"/demo/state?request_id={first}")
    assert replay.status_code == 200
    assert len(h.visitors.records["A"].receipts) == 256
    h.visitors.records["A"].expires_at = datetime(
        2000, 1, 1, tzinfo=timezone.utc
    )
    response = await h.client.get(f"/demo/state?request_id={first}")
    assert response.status_code == 410
    assert set(response.json()) == {"detail"}
    assert "set-cookie" not in response.headers
    response = await start(h, request_id=first)
    assert response.status_code == 410
    assert h.visitors.clones == 1


async def test_scope_ownership_unknown_id_and_state_get_do_not_acquire_db(
    harness,
):
    h = harness
    request_id = uid()
    await start(h, request_id=request_id)
    await h.client.get("/demo/state")
    deadline = h.visitors.records["A"].expires_at
    for path, status in (
        ("/demo/state", 200),
        (f"/demo/state?request_id={uid()}", 404),
        (f"/demo/state?request_id={request_id}", 200),
    ):
        response = await h.client.get(path)
        assert response.status_code == status
        assert ("set-cookie" in response.headers) is (path == "/demo/state")
    assert not h.security.contexts
    assert h.visitors.records["A"].expires_at == deadline
    h.client.cookies.clear()
    h.client.cookies.set("demo_session_id", "B", domain="demo.test", path="/")
    h.client.cookies.set("demo_csrftoken", "B-1", domain="demo.test", path="/")
    await start(h, "admin")
    response = await h.client.get(f"/demo/state?request_id={request_id}")
    assert response.status_code == 404
    assert "set-cookie" not in response.headers


async def test_prepare_expiry_wins_before_publish_and_late_fail_cannot_overwrite(
    harness,
):
    h = harness
    h.visitors.clone_continue.clear()
    request_id = uid()
    caller = asyncio.create_task(start(h, request_id=request_id))
    await h.visitors.clone_started.wait()
    record = h.visitors.records["A"]
    record.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    h.visitors.clone_continue.set()
    response = await caller
    assert response.status_code == 410
    assert record.state == "expired"
    assert record.current_ref is None


@pytest.mark.parametrize(
    "state,operation,code",
    [
        ("preparing", "role", "SESSION_REQUIRED"),
        ("preparing", "reset", "SESSION_REQUIRED"),
        ("prepare_failed", "role", "PREPARE_FAILED"),
        ("prepare_failed", "reset", "PREPARE_FAILED"),
        ("resetting", "start", "RESET_IN_PROGRESS"),
        ("resetting", "role", "RESET_IN_PROGRESS"),
        ("resetting", "reset", "RESET_IN_PROGRESS"),
        ("reset_failed", "start", "RESET_FAILED"),
        ("reset_failed", "role", "RESET_FAILED"),
    ],
)
async def test_new_operation_state_matrix_never_creates_work(
    harness, state, operation, code
):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    record = h.visitors.records["A"]
    record.state = state
    if state == "preparing":
        record.operation = SimpleNamespace(request_id=uid())
    if operation == "start":
        response = await start(h)
    elif operation == "reset":
        response = await reset(h)
    else:
        response = await h.client.post(
            "/demo/role",
            json={
                "request_id": uid(),
                "role": "teacher",
                "expected_generation": 1,
                "expected_auth_epoch": 1,
            },
            headers=headers(h.client),
        )
    assert response.json()["detail"]["code"] == f"DEMO_{code}"
    assert "set-cookie" not in response.headers
    assert h.visitors.clones == 1


@pytest.mark.parametrize("confirm", [False, None, "true", 1])
async def test_reset_requires_literal_true(harness, confirm):
    response = await reset(harness, confirm=confirm)
    assert response.status_code == 422
    assert not harness.security.contexts


async def test_shutdown_during_start_admission_does_not_launch_late_task(
    harness,
):
    h = harness
    entered, release = asyncio.Event(), asyncio.Event()
    reserve = h.visitors.reserve_start

    async def delayed(*args):
        result = await reserve(*args)
        entered.set()
        await release.wait()
        return result

    h.visitors.reserve_start = delayed
    caller = asyncio.create_task(start(h))
    await entered.wait()
    await h.session.shutdown()
    release.set()
    response = await caller
    assert response.status_code == 503
    assert h.visitors.clones == 0
    assert not h.session._tasks
    assert h.visitors.records["A"].state == "prepare_failed"


async def test_shutdown_during_reset_context_does_not_mark_or_leak_lease(
    harness,
):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    entered, release = asyncio.Event(), asyncio.Event()
    context = h.security.context

    async def delayed(*args):
        result = await context(*args)
        entered.set()
        await release.wait()
        return result

    h.security.context = delayed
    caller = asyncio.create_task(reset(h))
    await entered.wait()
    await h.session.shutdown()
    release.set()
    response = await caller
    assert response.status_code == 503
    assert h.visitors.records["A"].generation == 1
    assert h.visitors.records["A"].active_leases == 0
    assert h.visitors.clones == 1
    assert not h.session._tasks


async def test_role_sqlite_locked_failure_replays_retry_after(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    request_id = uid()
    command = {
        "request_id": request_id,
        "role": "teacher",
        "expected_generation": 1,
        "expected_auth_epoch": 1,
    }
    async with h.engine.connect() as locker:
        await locker.exec_driver_sql("BEGIN EXCLUSIVE")
        response = await h.client.post(
            "/demo/role", json=command, headers=headers(h.client)
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "DEMO_DB_LOCKED"
        assert response.headers["retry-after"] == "1"
        await locker.rollback()
    response = await h.client.post(
        "/demo/role", json=command, headers=headers(h.client)
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert h.visitors.records["A"].auth_epoch == 1


async def test_operation_failure_receipt_preserves_dynamic_retry_after(harness):
    h = harness
    h.visitors.clone_error = DemoError("CLEANUP_BACKLOG", retry_after=23)
    request_id = uid()
    response = await start(h, request_id=request_id)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "23"
    await h.client.get("/demo/state")
    response = await h.client.get(f"/demo/state?request_id={request_id}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "23"
    assert h.visitors.clones == 1


async def test_busy_precedes_deleted_target_and_noop_keeps_revision(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    record = h.visitors.records["A"]
    revision = record.csrf_revision
    async with h.factory() as db:
        await db.execute(text('DELETE FROM "user" WHERE id=2'))
        await db.commit()
    h.lifecycle.busy = True
    command = {
        "request_id": uid(),
        "role": "teacher",
        "expected_generation": 1,
        "expected_auth_epoch": 1,
    }
    response = await h.client.post(
        "/demo/role", json=command, headers=headers(h.client)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DEMO_BUSY"
    response = await h.client.post(
        "/demo/role",
        json={**command, "request_id": uid(), "role": "admin"},
        headers=headers(h.client),
    )
    assert response.status_code == 200
    assert record.csrf_revision == revision
    assert record.auth_epoch == 1


async def test_reset_rolls_back_pending_request_transaction_and_duplicate_release_is_noop(
    harness,
):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    context = h.security.context

    async def pending(*args):
        ctx = await context(*args)
        await ctx.db.execute(
            text("INSERT INTO \"user\" VALUES (99, 'teacher')")
        )
        return ctx

    h.security.context = pending
    response = await reset(h)
    assert response.status_code == 200
    async with h.factory() as db:
        assert (
            await db.scalar(text('SELECT id FROM "user" WHERE id=99')) is None
        )
    released = h.visitors.calls.count("release_lease")
    await h.request_db.release_request_db(h.security.contexts[-1])
    assert h.visitors.calls.count("release_lease") == released


@pytest.mark.parametrize("form", [False, True])
@pytest.mark.parametrize("operation", ["role", "reset"])
@pytest.mark.parametrize("invalid", ["extra", "uuid", "generation", "epoch"])
async def test_control_json_form_validation_before_lookup(
    harness, form, operation, invalid
):
    h = harness
    body = {
        "request_id": uid(),
        "expected_generation": 1,
        "expected_auth_epoch": 1,
        **(
            {"role": "teacher"}
            if operation == "role"
            else {"confirm": "true" if form else True}
        ),
    }
    if invalid == "extra":
        body["user_id"] = 99
    elif invalid == "uuid":
        body["request_id"] = "ABC"
    elif invalid == "generation":
        body["expected_generation"] = "true" if form else True
    else:
        body["expected_auth_epoch"] = -1
    response = await h.client.post(
        f"/demo/{operation}",
        headers=headers(h.client),
        **({"data": body} if form else {"json": body}),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DEMO_INVALID_REQUEST"
    assert "set-cookie" not in response.headers
    assert not h.visitors.records
    assert not h.security.contexts


async def test_reset_form_true_and_missing_confirmation(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    body = {
        "request_id": uid(),
        "expected_generation": 1,
        "expected_auth_epoch": 1,
    }
    missing = await h.client.post(
        "/demo/reset", data=body, headers=headers(h.client)
    )
    assert missing.status_code == 422
    response = await h.client.post(
        "/demo/reset",
        data={**body, "confirm": "true"},
        headers=headers(h.client),
    )
    assert response.status_code == 200
    assert response.json()["generation"] == 2

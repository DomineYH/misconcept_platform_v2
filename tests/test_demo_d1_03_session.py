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

    def fail(self, op, code):
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
            error.retry_after,
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
            for task in tuple(session._tasks.values()):
                task.cancel()
            await asyncio.gather(
                *session._tasks.values(), return_exceptions=True
            )
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

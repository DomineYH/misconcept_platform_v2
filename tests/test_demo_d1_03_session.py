"""Demo control HTTP contract; security doubles do not prove middleware."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from shutil import copyfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.api.routes.demo import (
    CookieValues,
    get_demo_security,
    get_demo_session,
    router,
)
from src.demo.database import DatabaseRoot
from src.demo.lifecycle import ExecutionToken, Lifecycle, TaskControl
from src.demo.session import (
    DemoError,
    DemoSession,
)
from src.demo.visitors import ControlContext, VisitorRegistry


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


async def make_root(tmp_path, *, max_db_bytes=None):
    """A tiny synthetic seed, using the official explicit-engine installer."""
    import hashlib
    import json
    import sqlite3

    from src.db.migrations.migrate import run_all_migrations
    from src.demo.database import SeedSpec

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
        for table in (
            "session_summary",
            "session_feedback_report",
            "generation_run",
            "message",
            "session",
        ):
            db.execute('DROP TABLE "' + table + '"')
        for ddl in (
            'CREATE TABLE "session" (id INTEGER PRIMARY KEY, deleted_at TEXT, ended_at TEXT, config_snapshot_json TEXT, config_hash TEXT, snapshot_origin TEXT, source_scenario_version INTEGER, snapshot_created_at TEXT)',
            "CREATE TABLE message (id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT, turn_id TEXT, turn_index INTEGER, generation_run_id TEXT)",
            "CREATE TABLE generation_run (id TEXT PRIMARY KEY, session_id INTEGER, turn_id TEXT, operation TEXT, status TEXT, result_kind TEXT, mentor_trigger TEXT, accepted_report_id INTEGER, accepted_report_version INTEGER)",
            "CREATE TABLE session_summary (id INTEGER PRIMARY KEY, session_id INTEGER)",
            "CREATE TABLE session_feedback_report (id INTEGER PRIMARY KEY, session_id INTEGER, version INTEGER, status TEXT, payload_json TEXT)",
        ):
            db.execute(ddl)
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


class SyntheticClock:
    def __init__(self):
        self.elapsed = 0
        self.initial = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def monotonic(self):
        return self.elapsed

    def utcnow(self):
        from datetime import timedelta

        return self.initial + timedelta(seconds=self.elapsed)


class ObservedRegistry(VisitorRegistry):
    """Real #191 authority; gates and trace only observe its operations."""

    def __init__(self, root, *, lifecycle):
        super().__init__(root, lifecycle=lifecycle, clock=SyntheticClock())
        self.clones = self.cancelled = 0
        self.cancelled_operations = set()
        self.clone_started = asyncio.Event()
        self.clone_continue = asyncio.Event()
        self.clone_continue.set()
        self.clone_error = self.admission_error = None
        self.calls = []
        self.bootstraps = {
            name: self.verify_bootstrap(name * 32) for name in ("A", "B")
        }
        clone = root.clone

        async def observed_clone(*args, **kwargs):
            assert not self.lock.locked()
            self.clones += 1
            self.clone_started.set()
            while not self.clone_continue.is_set():
                if kwargs["cancel_event"].is_set():
                    raise DemoError("PREPARE_FAILED")
                try:
                    await asyncio.wait_for(self.clone_continue.wait(), 0.05)
                except TimeoutError:
                    pass
            if self.clone_error:
                raise self.clone_error
            self.calls.append("clone")
            return await clone(*args, **kwargs)

        root.clone = observed_clone

    def bootstrap(self, value):
        return self.bootstraps[value] if isinstance(value, str) else value

    def verified(self, value):
        if isinstance(value, str) or hasattr(value, "bootstrap_nonce"):
            return self.visitor_for_bootstrap(self.bootstrap(value))
        return value

    @property
    def records(self):
        return {
            name: self._state(visitor)
            for name, bootstrap in self.bootstraps.items()
            if (visitor := self.visitor_for_bootstrap(bootstrap)) is not None
        }

    @property
    def lock(self):
        state = self.records.get("A")
        return state.lock if state else self._admission

    @asynccontextmanager
    async def transition(self, visitor):
        async with super().transition(self.verified(visitor)) as handle:
            self.calls.append("lock")
            for name in ("mark_reset", "change_role", "retain_lease"):
                original = getattr(handle, name)

                def observe(*args, _name=name, _original=original, **kwargs):
                    self.calls.append(_name)
                    return _original(*args, **kwargs)

                setattr(handle, name, observe)
            try:
                yield handle
            finally:
                self.calls.append("unlock")

    async def reserve_start(self, *args):
        if self.admission_error:
            raise self.admission_error
        return await super().reserve_start(*args)

    async def cancel_preparation(self, op):
        if op.op_id not in self.cancelled_operations:
            self.cancelled_operations.add(op.op_id)
            self.cancelled += 1
        self.calls.append("cancel_preparation")
        return await super().cancel_preparation(op)


class ObservedLifecycle(Lifecycle):
    """Real #192 lifecycle; trace operations and optionally inject BUSY."""

    def __init__(self, visitors):
        super().__init__(visitors)
        self.force_busy = False
        self.scopes = []

    def busy_locked(self, handle, scope):
        assert self.visitors.lock.locked()
        self.visitors.calls.append("busy_locked")
        return self.force_busy or super().busy_locked(handle, scope)

    def cancel_scope(self, scope):
        assert not self.visitors.lock.locked()
        self.scopes.append(scope)
        self.visitors.calls.append("cancel_scope")
        return super().cancel_scope(scope)

    async def drain(self, scope, timeout_seconds):
        assert not self.visitors.lock.locked()
        self.visitors.calls.append("drain")
        return await super().drain(scope, timeout_seconds)


class RequestDBDouble:
    """#194 handoff and close/release double; rollback uses a real session."""

    def __init__(self, visitors):
        self.visitors = visitors
        self.close_error = False

    def handoff_request_db(self, ctx):
        assert self.visitors.lock.locked()
        object.__setattr__(ctx, "handoff", True)
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
            await self.visitors.release_lease(ctx.lease)
        object.__setattr__(ctx, "released", True)
        self.visitors.calls.append("release_lease")


class SecurityDouble:
    """Signature/CSRF and request-DB injection; all contexts and leases are #191."""

    def __init__(self, visitors):
        self.visitors = visitors
        self.contexts = []
        self.verify_calls = 0

    async def verified_bootstrap(self, request, *, issue):
        value = request.cookies.get("demo_session_id")
        if value is None and issue:
            value = "A"
        if value not in self.visitors.bootstraps:
            raise DemoError("SESSION_REQUIRED")
        return self.visitors.bootstraps[value]

    def cookie_identity(self, bootstrap):
        bootstrap = self.visitors.bootstrap(bootstrap)
        name = next(
            key
            for key, item in self.visitors.bootstraps.items()
            if item is bootstrap
        )
        record = self.visitors.records.get(name)
        return name, record.csrf_revision if record else 0

    async def cookies(self, bootstrap):
        name, revision = self.cookie_identity(bootstrap)
        return CookieValues(name, f"{name}-{revision}", True)

    async def verify_csrf(self, request, bootstrap):
        self.verify_calls += 1
        name, revision = self.cookie_identity(bootstrap)
        token = f"{name}-{revision}"
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
        visitor = self.visitors.verified(bootstrap)
        snapshot = await self.visitors.snapshot(visitor)
        if snapshot.state == "preparing":
            raise DemoError("SESSION_REQUIRED")
        generation = expected_generation or snapshot.generation
        epoch = expected_auth_epoch or snapshot.auth_epoch
        if snapshot.state == "reset_failed" and request.url.path.endswith(
            "reset"
        ):
            ctx = ControlContext(visitor, generation, epoch)
        else:
            ref = await self.visitors.resolve_visit(visitor, generation)
            lease = await self.visitors.acquire_lease(ref, epoch)
            try:
                ctx = await self.visitors.make_context(
                    visitor, lease, user_id=1 if snapshot.role == "admin" else 2
                )
            except BaseException:
                await self.visitors.release_lease(lease)
                raise
            object.__setattr__(ctx, "db", ctx.factory())
            await ctx.db.execute(text("SELECT 1"))
        object.__setattr__(ctx, "released", False)
        object.__setattr__(ctx, "handoff", False)
        self.contexts.append(ctx)
        return ctx


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seed_template(tmp_path_factory):
    root = await make_root(tmp_path_factory.mktemp("control-seed"))
    try:
        yield root._seed
    finally:
        root.close()


@pytest.fixture
async def harness(tmp_path, seed_template):
    root = DatabaseRoot(tmp_path / "demo", initialize=True).open()
    seed_path = root.path / "seed.db"
    manifest_path = root.path / "manifest.json"
    copyfile(seed_template.path, seed_path)
    copyfile(seed_template.manifest_path, manifest_path)
    root.validate_seed(
        replace(seed_template, path=seed_path, manifest_path=manifest_path)
    )
    lifecycle = ObservedLifecycle(None)
    visitors = ObservedRegistry(root, lifecycle=lifecycle)
    lifecycle.visitors = visitors
    assert visitors.lifecycle is lifecycle
    assert lifecycle.visitors is visitors
    request_db = RequestDBDouble(visitors)
    session = DemoSession(
        visitors, lifecycle, request_db, tour_content=TourContentDouble()
    )
    security = SecurityDouble(visitors)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_demo_security] = lambda: security
    app.dependency_overrides[get_demo_session] = lambda: session

    class Harness(SimpleNamespace):
        @property
        def factory(self):
            return visitors.records["A"].ref.factory

        @property
        def engine(self):
            return self.factory.engine

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://demo.test"
    ) as client:
        item = Harness(
            app=app,
            client=client,
            visitors=visitors,
            lifecycle=lifecycle,
            request_db=request_db,
            session=session,
            security=security,
            root=root,
        )
        try:
            await client.get("/demo")
            visitors.calls.clear()
            yield item
        finally:
            visitors.clone_continue.set()
            await session.shutdown()
            for ctx in security.contexts:
                request_db.close_error = False
                await request_db.release_request_db(ctx)
            for token in tuple(lifecycle._tokens.values()):
                token.task_control.done.set()
                await lifecycle.pop(*token.key, token)
            await visitors.shutdown()
            app.dependency_overrides.clear()
            root.close()


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
    assert (
        state["next_url"]
        == f"/scenarios?ds={state['browser_scope_id']}&dg=1&da=1"
    )
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
    assert h.visitors.records["A"].attempt_no == 2


async def test_role_busy_noop_stale_and_deleted_teacher(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    h.lifecycle.force_busy = True
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
    h.lifecycle.force_busy = False
    h.visitors.calls.clear()
    response = await h.client.post(
        "/demo/role",
        data={**command, "request_id": uid(), "role": "teacher"},
        headers=headers(h.client),
    )
    assert response.status_code == 200
    assert response.json()["auth_epoch"] == 2
    change = h.visitors.calls.index("change_role")
    assert h.visitors.calls[change - 1 : change + 1] == [
        "busy_locked",
        "change_role",
    ]
    assert h.visitors.calls.count("change_role") == 1
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
    async with setup_db(h) as db:
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
    assert set(h.lifecycle.scopes) == {
        (h.visitors.records["A"].verified.visitor_id, 1)
    }
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
    other = await reset(h, generation=2, epoch=2)
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
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/state"))
    ctx = await h.security.context(request, "A", 1, 1)
    h.session.drain_seconds = 1
    async with h.lifecycle.child_context(ctx) as child_ctx:
        await h.request_db.release_request_db(ctx)
        assert child_ctx.lease is not ctx.lease
        assert h.visitors.lease_count(ctx.scope) == 1
        request_id = uid()
        response = await reset(h, request_id)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "DEMO_DRAIN_TIMEOUT"
        assert h.visitors.clones == 1
        assert child_ctx.factory.retired
        assert not child_ctx.factory.disposed
        assert not child_ctx.lease.released
        assert h.visitors.lease_count(ctx.scope) == 1
        state = await h.client.get("/demo/state")
        assert state.json()["state"] == "reset_failed"
        assert state.json()["generation"] == state.json()["auth_epoch"] == 2
        replay = await reset(h, request_id)
        assert replay.status_code == 503
        assert replay.json()["result"]["detail"]["code"] == "DEMO_DRAIN_TIMEOUT"
        assert h.visitors.clones == 1
    assert child_ctx.lease.released
    assert h.visitors.lease_count(ctx.scope) == 0
    stale = await reset(h)
    assert stale.json()["detail"]["code"] == "DEMO_STALE_GENERATION"
    retry = await reset(h, generation=2, epoch=2)
    assert retry.status_code == 200
    assert retry.json()["generation"] == retry.json()["auth_epoch"] == 2
    assert child_ctx.factory.disposed
    assert h.visitors.records["A"].attempt_no == 3


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
    assert (
        h.visitors.lease_count((h.visitors.records["A"].verified.visitor_id, 1))
        == 1
    )
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

    seed_refs = {"admin": 1, "teacher": 2, "completed_session": 1}
    first_inputs = frozenset({"A", "B", "C"})
    common_input = "common"

    def supported_snapshot(self, row):
        return (
            row["config_hash"] == "supported"
            and row["snapshot_origin"] == "native"
        )


@asynccontextmanager
async def setup_db(h):
    """Fixture SQL bypasses the guarded application factory explicitly."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{h.factory.path}")
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db
    finally:
        await engine.dispose()


async def tour_tables(h):
    # Synthetic tour rows use the same physical generation as every HTTP path.
    assert h.factory is h.visitors.records["A"].ref.factory


async def tour_session(h, sid, *, supported=True, deleted=False, ended=False):
    async with setup_db(h) as db:
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
    async with setup_db(h) as db:
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
    async with setup_db(h) as db:
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
        "browser_scope_id": h.visitors.records["A"].browser_scope_id,
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
    async with setup_db(h) as db:
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
    h.session.tour_content = TourContentDouble()
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
            projection = await capture_tour_facts(
                ctx, handle, h.session.tour_content
            )
            assert projection.analysis_ready
            # Capture itself retains nothing, including on a rollback path.
            async with setup_db(h) as db:
                await db.execute(
                    text(
                        "UPDATE \"session\" SET deleted_at='2026-01-02' WHERE id=2"
                    )
                )
                await db.rollback()
            async with setup_db(h) as db:
                await db.execute(
                    text(
                        "UPDATE \"session\" SET deleted_at='2026-01-02' WHERE id=2"
                    )
                )
                await db.commit()
            tombstone = await capture_tour_facts(
                ctx, handle, h.session.tour_content
            )
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
    async with setup_db(h) as db:
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
    h.visitors.clock.elapsed += 7201
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
    deadline = (await h.visitors.snapshot(h.visitors.verified("A"))).expires_at
    for path, status in (
        ("/demo/state", 200),
        (f"/demo/state?request_id={uid()}", 404),
        (f"/demo/state?request_id={request_id}", 200),
    ):
        response = await h.client.get(path)
        assert response.status_code == status
        assert ("set-cookie" in response.headers) is (path == "/demo/state")
    assert not h.security.contexts
    assert (
        await h.visitors.snapshot(h.visitors.verified("A"))
    ).expires_at == deadline
    h.client.cookies.clear()
    h.client.cookies.set("demo_session_id", "B", domain="demo.test", path="/")
    h.client.cookies.set("demo_csrftoken", "B-0", domain="demo.test", path="/")
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
    h.visitors.clock.elapsed += 7201
    h.visitors.clone_continue.set()
    response = await caller
    assert response.status_code == 410
    assert record.state == "expired"
    assert record.ref is None


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
    assert len(h.visitors.records["A"].leases) == 0
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
    async with h.engine.connect() as connection:
        # Configure the fixture connection for an actual exclusive-reader lock.
        raw = await connection.get_raw_connection()
        cursor = await raw.driver_connection.execute(
            "PRAGMA journal_mode=DELETE"
        )
        await cursor.close()
    async with setup_db(h) as locker:
        await locker.execute(text("BEGIN EXCLUSIVE"))
        response = await h.client.post(
            "/demo/role", json=command, headers=headers(h.client)
        )
        await locker.rollback()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DEMO_DB_LOCKED"
    assert response.headers["retry-after"] == "1"
    async with setup_db(h) as db:
        assert (
            await db.scalar(text('SELECT role FROM "user" WHERE id=2'))
            == "teacher"
        )
    response = await h.client.post(
        "/demo/role", json=command, headers=headers(h.client)
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert h.visitors.records["A"].auth_epoch == 1

    receipt = await h.visitors.receipt(h.visitors.verified("A"), request_id)
    assert receipt.error_code == "DEMO_DB_LOCKED"
    assert receipt.status_code == 503
    assert receipt.retry_after == 1
    assert response.json()["result"]["detail"]["code"] == "DEMO_DB_LOCKED"


async def test_operation_failure_receipt_preserves_dynamic_retry_after(
    harness, monkeypatch
):
    h = harness
    from src.demo.visitors import TransitionHandle

    fail = TransitionHandle.fail
    observed = []

    def strict_fail(handle, op, code, *, error=None):
        observed.append((code, error))
        return fail(handle, op, code, error=error)

    monkeypatch.setattr(TransitionHandle, "fail", strict_fail)
    failure = DemoError("CLEANUP_BACKLOG", retry_after=23)
    h.visitors.clone_error = failure
    request_id = uid()
    response = await start(h, request_id=request_id)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "23"
    await h.client.get("/demo/state")
    response = await h.client.get(f"/demo/state?request_id={request_id}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "23"
    assert h.visitors.clones == 1
    assert observed == [("DEMO_CLEANUP_BACKLOG", failure)]
    receipt = await h.visitors.receipt(h.visitors.verified("A"), request_id)
    assert receipt.error_code == "DEMO_CLEANUP_BACKLOG"
    assert receipt.status_code == 503
    assert receipt.retry_after == 23
    assert receipt.result.state == "prepare_failed"


async def test_busy_precedes_deleted_target_and_noop_keeps_revision(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    record = h.visitors.records["A"]
    revision = record.csrf_revision
    async with setup_db(h) as db:
        await db.execute(text('DELETE FROM "user" WHERE id=2'))
        await db.commit()
    h.lifecycle.force_busy = True
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

    async with setup_db(h) as pending_db:

        async def pending(*args):
            ctx = await context(*args)
            await ctx.db.close()
            object.__setattr__(ctx, "db", pending_db)
            await ctx.db.execute(
                text(
                    "INSERT INTO \"user\" (id,username,nickname,password_hash,role,created_at) VALUES (99, 'pending', 'Pending', '!disabled', 'teacher', '2026-10-19')"
                )
            )
            return ctx

        h.security.context = pending
        response = await reset(h)
    assert response.status_code == 200
    async with setup_db(h) as db:
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


@pytest.mark.parametrize("operation", ["role", "reset"])
async def test_receipt_wins_if_context_becomes_stale_after_preflight(
    harness, operation
):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    entered, release = asyncio.Event(), asyncio.Event()
    context = h.security.context
    request_id = uid()
    body = {
        "request_id": request_id,
        "expected_generation": 1,
        "expected_auth_epoch": 1,
        **({"role": "teacher"} if operation == "role" else {"confirm": True}),
    }

    async def delayed(request, bootstrap, generation, epoch):
        entered.set()
        await release.wait()
        record = h.visitors._state(h.visitors.verified(bootstrap))
        if record.generation != generation:
            raise DemoError("STALE_GENERATION")
        if record.auth_epoch != epoch:
            raise DemoError("STALE_ROLE")
        return await context(request, bootstrap, generation, epoch)

    h.security.context = delayed
    first = asyncio.create_task(
        h.client.post(
            f"/demo/{operation}", json=body, headers=headers(h.client)
        )
    )
    await entered.wait()
    h.security.context = context
    try:
        completed = await h.client.post(
            f"/demo/{operation}", json=body, headers=headers(h.client)
        )
        assert completed.status_code == 200
        release.set()
        replay = await first
        assert replay.status_code == 200
        assert replay.json()["result"]["auth_epoch"] == 2
        assert h.visitors.records["A"].auth_epoch == 2
        assert h.visitors.clones == (2 if operation == "reset" else 1)
        assert "set-cookie" not in replay.headers
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_in_progress_receipt_wins_if_context_observes_resetting(harness):
    h = harness
    await start(h)
    await h.client.get("/demo/state")
    entered, release = asyncio.Event(), asyncio.Event()
    context = h.security.context
    request_id = uid()

    async def delayed(*args):
        entered.set()
        await release.wait()
        return await context(*args)

    h.security.context = delayed
    first = asyncio.create_task(reset(h, request_id))
    await entered.wait()
    h.security.context = context
    h.visitors.clone_started.clear()
    h.visitors.clone_continue.clear()
    owner = asyncio.create_task(reset(h, request_id))
    try:
        await h.visitors.clone_started.wait()
        release.set()
        response = await first
        assert response.status_code == 202
        assert response.json()["request_id"] == request_id
        assert response.json()["state"] == "resetting"
        assert "set-cookie" not in response.headers
        h.visitors.clone_continue.set()
        assert (await owner).status_code == 200
        assert h.visitors.clones == 2
    finally:
        release.set()
        h.visitors.clone_continue.set()
        await asyncio.gather(first, owner, return_exceptions=True)


async def test_registration_first_blocks_role_and_preserves_token(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/role"))
    ctx = await h.security.context(request, "A", 1, 1)
    cancel_requested = asyncio.Event()
    control = TaskControl(cancel_requested.set, asyncio.Event())
    resource_id = uid()
    scope = ctx.scope
    transition = h.visitors.transition
    lifecycle_transition = h.lifecycle.transition
    role_waiting = asyncio.Event()
    registered, release_registration = asyncio.Event(), asyncio.Event()
    registration = role = token = None

    @asynccontextmanager
    async def hold_registered(context):
        async with lifecycle_transition(context) as handle:
            yield handle
            registered.set()
            await release_registration.wait()

    @asynccontextmanager
    async def observe_role_waiting(visitor):
        role_waiting.set()
        async with transition(visitor) as handle:
            yield handle

    h.lifecycle.transition = hold_registered
    try:
        registration = asyncio.create_task(
            h.lifecycle.register(ctx, "run", resource_id, control)
        )
        await asyncio.wait_for(registered.wait(), 10)
        token = h.lifecycle.lookup(scope, "run", resource_id)
        assert isinstance(token, ExecutionToken)
        child = token.registry_lease
        assert h.lifecycle.tokens(scope) == (token,)
        assert child is not ctx.lease
        assert h.visitors.lease_count(scope) == 2
        assert h.visitors.lock.locked()
        assert h.visitors.calls.count("retain_lease") == 1
        h.visitors.calls.clear()
        h.visitors.transition = observe_role_waiting
        role = asyncio.create_task(
            h.client.post(
                "/demo/role",
                json={
                    "request_id": uid(),
                    "role": "teacher",
                    "expected_generation": 1,
                    "expected_auth_epoch": 1,
                },
                headers=headers(h.client),
            )
        )
        await asyncio.wait_for(role_waiting.wait(), 10)
        assert h.visitors.lock.locked()
        release_registration.set()
        assert await asyncio.wait_for(registration, 10) is token
        response = await asyncio.wait_for(role, 10)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DEMO_BUSY"
        assert h.visitors.records["A"].role == "admin"
        assert h.visitors.records["A"].auth_epoch == 1
        assert h.lifecycle.tokens(scope) == (token,)
        assert h.lifecycle.lookup(scope, "run", resource_id) is token
        assert token.registry_lease is child
        assert not child.released
        assert not token.completed
        assert not cancel_requested.is_set()
        assert h.lifecycle.scopes == []
        assert "retain_lease" not in h.visitors.calls
        assert "busy_locked" in h.visitors.calls
        assert "change_role" not in h.visitors.calls
        assert h.visitors.lease_count(scope) == 2
    finally:
        release_registration.set()
        await asyncio.wait_for(
            asyncio.gather(
                *(task for task in (registration, role) if task is not None),
                return_exceptions=True,
            ),
            10,
        )
        h.visitors.transition = transition
        h.lifecycle.transition = lifecycle_transition
        token = h.lifecycle.lookup(scope, "run", resource_id)
        if token is not None:
            control.done.set()
            assert await h.lifecycle.pop(*token.key, token)
            assert token.registry_lease.released
        await h.request_db.release_request_db(ctx)


async def test_role_first_rejects_old_registration_without_token_or_child(
    harness,
):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/role"))
    old_ctx = await h.security.context(request, "A", 1, 1)
    control = TaskControl(asyncio.Event().set, asyncio.Event())
    scope = old_ctx.scope
    resource_id = uid()
    transition, context = h.visitors.transition, h.security.context
    lifecycle_transition = h.lifecycle.transition
    role_locked, release_role = asyncio.Event(), asyncio.Event()
    registration_attempted = asyncio.Event()
    role = registration = None
    pause_role = True

    @asynccontextmanager
    async def hold_role_handle(visitor):
        nonlocal pause_role
        async with transition(visitor) as handle:
            if pause_role:
                pause_role = False
                role_locked.set()
                await release_role.wait()
            yield handle

    @asynccontextmanager
    async def observe_registration_attempt(context):
        registration_attempted.set()
        async with lifecycle_transition(context) as handle:
            yield handle

    async def role_context(*args):
        ctx = await context(*args)
        h.visitors.transition = hold_role_handle
        return ctx

    h.lifecycle.transition = observe_registration_attempt
    try:
        assert h.visitors.lease_count(scope) == 1
        assert not h.lifecycle.tokens(scope)
        h.security.context = role_context
        h.visitors.calls.clear()
        role = asyncio.create_task(
            h.client.post(
                "/demo/role",
                json={
                    "request_id": uid(),
                    "role": "teacher",
                    "expected_generation": 1,
                    "expected_auth_epoch": 1,
                },
                headers=headers(h.client),
            )
        )
        await asyncio.wait_for(role_locked.wait(), 10)
        assert h.visitors.lock.locked()
        registration = asyncio.create_task(
            h.lifecycle.register(old_ctx, "run", resource_id, control)
        )
        await asyncio.wait_for(registration_attempted.wait(), 10)
        assert not h.lifecycle.tokens(scope)
        # The held role request and old request each own one parent lease.
        assert h.visitors.lease_count(scope) == 2
        release_role.set()
        response = await asyncio.wait_for(role, 10)
        assert response.status_code == 200
        assert response.json()["role"] == "teacher"
        assert response.json()["auth_epoch"] == 2
        with pytest.raises(DemoError) as error:
            await asyncio.wait_for(registration, 10)
        assert error.value.code == "DEMO_STALE_ROLE"
        assert "retain_lease" not in h.visitors.calls
        assert not h.lifecycle.tokens(scope)
        assert h.lifecycle.lookup(scope, "run", resource_id) is None
        assert h.visitors.lease_count(scope) == 1
        change = h.visitors.calls.index("change_role")
        assert h.visitors.calls[change - 1 : change + 1] == [
            "busy_locked",
            "change_role",
        ]
        assert h.visitors.calls.count("change_role") == 1
    finally:
        release_role.set()
        await asyncio.wait_for(
            asyncio.gather(
                *(task for task in (role, registration) if task is not None),
                return_exceptions=True,
            ),
            10,
        )
        h.visitors.transition = transition
        h.security.context = context
        h.lifecycle.transition = lifecycle_transition
        token = h.lifecycle.lookup(scope, "run", resource_id)
        if token is not None:
            control.done.set()
            await h.lifecycle.pop(*token.key, token)
        await h.request_db.release_request_db(old_ctx)


async def test_preacceptance_errors_never_create_control_receipts(harness):
    h = harness
    await start(h, "admin")
    await h.client.get("/demo/state")
    state = h.visitors.records["A"]
    initial = dict(state.receipts)
    base = {
        "request_id": uid(),
        "role": "teacher",
        "expected_generation": 1,
        "expected_auth_epoch": 1,
    }
    for changes, status, code, csrf in (
        ({}, 403, "CSRF_INVALID", False),
        ({"extra": True}, 422, "INVALID_REQUEST", True),
        ({"expected_generation": 2}, 409, "STALE_GENERATION", True),
        ({"expected_auth_epoch": 2}, 409, "STALE_ROLE", True),
    ):
        request_id = uid()
        response = await h.client.post(
            "/demo/role",
            json={**base, "request_id": request_id, **changes},
            headers=(
                headers(h.client) if csrf else {"origin": "https://demo.test"}
            ),
        )
        assert response.status_code == status
        assert response.json()["detail"]["code"] == f"DEMO_{code}"
        assert request_id not in state.receipts
        assert state.receipts == initial
    for _ in range(255):
        assert (await start(h, "admin")).status_code == 200
    request_id = uid()
    response = await h.client.post(
        "/demo/role",
        json={**base, "request_id": request_id},
        headers=headers(h.client),
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "DEMO_LIMIT_REACHED"
    assert request_id not in state.receipts
    assert len(state.receipts) == 256


async def test_start_nonleader_rechecks_receipt_after_concurrent_role_change(
    harness,
):
    h = harness
    reserve = h.visitors.reserve_start
    entered, allow_reserve = asyncio.Event(), asyncio.Event()
    reserved, allow_return = asyncio.Event(), asyncio.Event()
    first_call = True
    request_id = uid()
    caller = None

    async def delayed_reserve(*args):
        nonlocal first_call
        if not first_call:
            return await reserve(*args)
        first_call = False
        entered.set()
        await allow_reserve.wait()
        reservation = await reserve(*args)
        assert not reservation.leader
        reserved.set()
        await allow_return.wait()
        return reservation

    h.visitors.reserve_start = delayed_reserve
    try:
        caller = asyncio.create_task(start(h, "teacher", request_id))
        await asyncio.wait_for(entered.wait(), 10)
        completed = await start(h, "teacher", request_id)
        assert completed.status_code == 201
        allow_reserve.set()
        await asyncio.wait_for(reserved.wait(), 10)
        await h.client.get("/demo/state")
        changed = await h.client.post(
            "/demo/role",
            json={
                "request_id": uid(),
                "role": "admin",
                "expected_generation": 1,
                "expected_auth_epoch": 1,
            },
            headers=headers(h.client),
        )
        assert changed.status_code == 200
        assert changed.json()["auth_epoch"] == 2
        allow_return.set()
        response = await asyncio.wait_for(caller, 10)
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["historical"] is True
        assert receipt["result"]["role"] == "teacher"
        assert receipt["result"]["auth_epoch"] == 1
        assert receipt["result"]["next_url"] is None
        assert receipt["current"]["role"] == "admin"
        assert receipt["current"]["auth_epoch"] == 2
        assert h.visitors.clones == 1
        assert "set-cookie" not in response.headers
    finally:
        allow_reserve.set()
        allow_return.set()
        if caller is not None:
            await asyncio.wait_for(
                asyncio.gather(caller, return_exceptions=True), 10
            )
        h.visitors.reserve_start = reserve


async def test_start_failure_settled_before_reserve_returns_recorded_receipt(
    harness,
):
    h = harness
    reserve = h.visitors.reserve_start
    entered, release = asyncio.Event(), asyncio.Event()
    first_call = True
    request_id = uid()
    caller = None
    h.visitors.clone_error = DemoError("DB_LOCKED", retry_after=7)

    async def delayed_reserve(*args):
        nonlocal first_call
        if first_call:
            first_call = False
            entered.set()
            await release.wait()
        return await reserve(*args)

    h.visitors.reserve_start = delayed_reserve
    try:
        caller = asyncio.create_task(start(h, "teacher", request_id))
        await asyncio.wait_for(entered.wait(), 10)
        failed = await start(h, "teacher", request_id)
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "DEMO_DB_LOCKED"
        assert failed.headers["retry-after"] == "7"
        release.set()
        response = await asyncio.wait_for(caller, 10)
        assert response.status_code == 503
        assert response.headers["retry-after"] == "7"
        receipt = response.json()
        assert receipt["operation"] == "start"
        assert receipt["request_id"] == request_id
        assert receipt["historical"] is False
        assert receipt["result"]["detail"]["code"] == "DEMO_DB_LOCKED"
        assert receipt["current"]["state"] == "prepare_failed"
        assert h.visitors.clones == 1
        assert len(h.visitors.records["A"].receipts) == 1
        assert "set-cookie" not in response.headers
    finally:
        release.set()
        if caller is not None:
            await asyncio.wait_for(
                asyncio.gather(caller, return_exceptions=True), 10
            )
        h.visitors.reserve_start = reserve


async def test_expiry_discards_facts_without_state_poll(harness):
    import gc
    import weakref

    from src.demo.session import _tour_cache

    h = harness
    response = await start(h, "teacher")
    assert response.status_code == 201
    scope = response.json()["browser_scope_id"]
    visitor = h.visitors.verified("A")
    generation = weakref.ref(h.visitors.records["A"].ref)
    assert any(
        facts.browser_scope_id == scope for facts in _tour_cache.values()
    )

    h.visitors.clock.elapsed = h.visitors.settings.idle_ttl_seconds
    await h.visitors.cleanup_tick()
    assert h.visitors.records["A"].state == "expired"

    h.visitors.clock.elapsed = h.visitors.settings.absolute_ttl_seconds
    await h.visitors.cleanup_tick()
    assert visitor.visitor_id not in h.visitors._states
    assert h.visitors.reserved_visitors == 0
    gc.collect()
    assert generation() is None
    assert not any(
        facts.browser_scope_id == scope for facts in _tour_cache.values()
    )


async def test_expired_generation_cannot_read_retained_facts(harness):
    from src.demo.session import capture_tour_facts

    h = harness
    assert (await start(h, "teacher")).status_code == 201
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/state"))
    ctx = await h.security.context(request, "A", 1, 1)
    await h.request_db.release_request_db(ctx)
    h.visitors.clock.elapsed = h.visitors.settings.idle_ttl_seconds
    await h.visitors.cleanup_tick()
    assert ctx.lease._ref.factory.retired
    async with h.visitors.transition(ctx.verified_visitor) as handle:
        with pytest.raises(DemoError, match="DEMO_EXPIRED"):
            await capture_tour_facts(ctx, handle, h.session.tour_content)


async def test_expiry_between_context_check_and_facts_lookup_is_canonical(
    harness,
):
    from src.demo.session import capture_tour_facts

    h = harness
    assert (await start(h, "teacher")).status_code == 201
    request = SimpleNamespace(url=SimpleNamespace(path="/demo/state"))
    ctx = await h.security.context(request, "A", 1, 1)
    try:
        async with h.visitors.transition(ctx.verified_visitor) as handle:
            check = handle.check

            def check_then_expire(context):
                snapshot = check(context)
                h.visitors.clock.elapsed = h.visitors.settings.idle_ttl_seconds
                return snapshot

            handle.check = check_then_expire
            with pytest.raises(DemoError, match="DEMO_EXPIRED"):
                await capture_tour_facts(ctx, handle, h.session.tour_content)
    finally:
        await h.request_db.release_request_db(ctx)

"""Real demo request integration, isolated from the normal-mode test process."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "signature",
        "isolation",
        "namespace",
        "security",
        "user_and_expiry",
        "read_reset",
        "late_write",
        "cleanup",
        "reset_order",
        "child_context",
        "write_serialization",
        "reset_close_failure",
        "restart_recovery",
        "malformed_form",
        "rollback_failure",
        "captured_connection",
        "typed_namespace_priority",
        "control_selectors",
        "signer_lifecycle_matrix",
    ],
)
def test_demo_request_boundary(case, tmp_path):
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
        DEMO_ORIGIN="https://demo.test",
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET="s" * 48,
        DEMO_CSRF_SECRET="c" * 48,
        SESSION_SECRET="n" * 48,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
    )
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


class Clock:
    def __init__(self):
        self.elapsed = 0

    def monotonic(self):
        return self.elapsed

    def utcnow(self):
        from datetime import datetime, timedelta, timezone

        return datetime(2026, 10, 19, tzinfo=timezone.utc) + timedelta(
            seconds=self.elapsed
        )


class Harness:
    async def open(self, tmp_path):
        self.root = await make_root(tmp_path)
        self.clock = Clock()
        self.lifecycle = Lifecycle()
        self.visitors = VisitorRegistry(
            self.root, lifecycle=self.lifecycle, clock=self.clock
        )
        self.lifecycle.visitors = self.visitors
        self.runtime = deps.DemoRequestDB(
            self.visitors,
            self.lifecycle,
            seed_refs={"admin": 1, "teacher": 2},
        )
        self.session = DemoSession(
            self.visitors,
            self.lifecycle,
            self.runtime,
            tour_content=SimpleNamespace(seed_refs={"admin": 1, "teacher": 2}),
            drain_seconds=1,
        )
        self.contexts = []
        self.clients = []
        self.read_entered = asyncio.Event()
        self.read_continue = asyncio.Event()
        app = self.app = FastAPI()
        app.state.demo_request_db = self.runtime
        app.state.demo_session = self.session
        app.include_router(demo.router)
        for route in app.routes:
            if isinstance(route, APIRoute):
                route.app = request_response(
                    deps.demo_request_handler(route.get_route_handler())
                )
        app.dependency_overrides[demo.get_demo_security] = (
            deps.get_demo_security
        )
        app.dependency_overrides[demo.get_demo_session] = deps.get_demo_session
        app.router.route_class = deps.DemoRequestRoute

        @app.get("/records")
        @app.get("/records/{record_id}")
        async def records(
            request: Request,
            user=Depends(deps.get_current_user),
            db=Depends(deps.get_db_session),
        ):
            value = (
                await db.execute(
                    text("SELECT body FROM synthetic_record WHERE id=1")
                )
            ).scalar_one()
            if request.query_params.get("wait") == "true":
                self.read_entered.set()
                await self.read_continue.wait()
            return {
                "user_id": user.id,
                "nickname": user.nickname,
                "body": value,
            }

        @app.post("/records/{record_id}")
        async def update_record(
            request: Request,
            user=Depends(deps.get_current_user),
            db=Depends(deps.get_db_session),
        ):
            value = await request.json()
            await db.execute(
                text("UPDATE synthetic_record SET body=:body WHERE id=1"), value
            )
            return {"user_id": user.id, "body": value["body"]}

        class StrictForm(BaseModel):
            model_config = ConfigDict(extra="forbid")
            body: str

        @app.post("/typed")
        async def typed_record(
            value: StrictForm,
            user=Depends(deps.get_current_user),
            db=Depends(deps.get_db_session),
        ):
            await db.execute(
                text("UPDATE synthetic_record SET body=:body WHERE id=1"),
                {"body": value.body},
            )
            return {"body": value.body, "user_id": user.id}

        @app.post("/form")
        async def form_record(
            value: Annotated[StrictForm, Form()],
            user=Depends(deps.get_current_user),
            db=Depends(deps.get_db_session),
        ):
            await db.execute(
                text("UPDATE synthetic_record SET body=:body WHERE id=1"),
                {"body": value.body},
            )
            return {"body": value.body, "user_id": user.id}

        @app.get("/admin")
        async def admin(user=Depends(deps.get_admin_user)):
            return {"user_id": user.id}

        return self

    async def client(self):
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="https://demo.test"
        )
        self.clients.append(client)
        return client

    async def start(self, client, role="teacher"):
        response = await client.get("/demo")
        assert response.status_code == 200, response.text
        command = {"request_id": str(uuid4()), "role": role}
        response = await client.post(
            "/demo/start", json=command, headers=self.headers(client)
        )
        assert response.status_code == 201, response.text
        state = response.json()
        response = await client.get("/demo/state")
        assert response.status_code == 200, response.text
        return state

    def headers(self, client):
        return {
            "origin": "https://demo.test",
            "x-csrf-token": client.cookies.get("demo_csrftoken"),
        }

    def namespace(self, state):
        return {
            "ds": state["browser_scope_id"],
            "dg": state["generation"],
            "da": state["auth_epoch"],
        }

    async def context(self, client, state):
        from urllib.parse import urlencode

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "path": "/records",
                "query_string": urlencode(self.namespace(state)).encode(),
                "headers": [
                    (
                        b"cookie",
                        f"demo_session_id={client.cookies.get('demo_session_id')}".encode(),
                    )
                ],
                "app": self.app,
            }
        )
        security = deps.get_demo_security(request)
        bootstrap = await security.verified_bootstrap(request, issue=False)
        ctx = await security.context(
            request, bootstrap, state["generation"], state["auth_epoch"]
        )
        self.contexts.append(ctx)
        return ctx

    async def reset(self, client, state, request_id=None):
        return await client.post(
            "/demo/reset",
            json={
                "request_id": request_id or str(uuid4()),
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
                "confirm": True,
            },
            headers=self.headers(client),
        )

    async def close(self):
        self.read_continue.set()
        await self.session.shutdown()
        for ctx in self.contexts:
            await self.runtime.release_request_db(ctx)
        for client in self.clients:
            await client.aclose()
        await self.visitors.shutdown()
        self.root.close()


def error(response, code):
    assert response.status_code >= 400, response.text
    assert response.json()["detail"]["code"] == "DEMO_" + code, response.text
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"


async def signature(h):
    client = await h.client()
    error(await client.get("/records"), "SESSION_REQUIRED")
    client.cookies.set("demo_session_id", "forged")
    error(await client.get("/records"), "SESSION_REQUIRED")
    error(
        await client.post(
            "/demo/start", json={}, headers={"origin": "https://evil.test"}
        ),
        "SESSION_REQUIRED",
    )
    assert not h.visitors._states
    del h.app.state.demo_request_db
    error(await client.get("/records"), "CONFIGURATION_INVALID")


async def isolation(h):
    a, b = await h.client(), await h.client()
    sa, sb = await h.start(a), await h.start(b)
    assert sa["browser_scope_id"] != sb["browser_scope_id"]
    for client, state, body in ((a, sa, "only-A"), (b, sb, "only-B")):
        response = await client.post(
            "/records/1",
            params=h.namespace(state),
            json={"body": body},
            headers=h.headers(client),
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == 2
    for client, state, expected in ((a, sa, "only-A"), (b, sb, "only-B")):
        response = await client.get("/records/1", params=h.namespace(state))
        assert response.status_code == 200, response.text
        assert response.json()["body"] == expected
        for selector, value in {
            "visitor_id": sb["browser_scope_id"],
            "user_id": 1,
            "db_path": str(h.root.path / "seed.db"),
            "database_url": "sqlite+aiosqlite:///"
            + str(h.root.path / "seed.db"),
        }.items():
            response = await client.get(
                "/records/1", params=h.namespace(state) | {selector: value}
            )
            error(response, "INVALID_REQUEST")
    error(await a.get("/records/1", params=h.namespace(sb)), "STALE_GENERATION")
    error(await a.get("/records/1"), "STALE_GENERATION")
    for params, code in (
        (h.namespace(sa) | {"dg": sa["generation"] + 1}, "STALE_GENERATION"),
        (h.namespace(sa) | {"da": sa["auth_epoch"] + 1}, "STALE_ROLE"),
        (h.namespace(sa) | {"ds": "../../seed.db"}, "STALE_GENERATION"),
    ):
        response = await a.get("/records/1", params=params)
        error(response, code)
        assert response.json()["detail"]["generation"] == sa["generation"]
        assert response.json()["detail"]["auth_epoch"] == sa["auth_epoch"]
    ctx = await h.context(a, sa)
    await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 0
    response = await a.get("/admin")
    assert response.status_code == 403, response.text
    assert h.visitors.lease_count(ctx.scope) == 0


async def namespace(h):
    client = await h.client()
    state = await h.start(client)
    ns = h.namespace(state)
    headers = h.headers(client) | {
        "x-demo-scope": ns["ds"],
        "x-demo-generation": str(ns["dg"]),
        "x-demo-auth-epoch": str(ns["da"]),
    }
    response = await client.post(
        "/form",
        params=ns,
        headers=headers,
        data={
            "body": "stripped",
            "_demo_scope": ns["ds"],
            "_demo_generation": ns["dg"],
            "_demo_auth_epoch": ns["da"],
        },
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/form",
        params=ns,
        headers=headers,
        data={
            "body": "forbidden",
            "_demo_scope": "wrong",
            "_demo_generation": ns["dg"],
            "_demo_auth_epoch": ns["da"],
        },
    )
    assert response.status_code == 409, response.text
    error(response, "STALE_GENERATION")
    response = await client.get(
        "/records", params=ns, headers={"x-demo-generation": str(ns["dg"] + 1)}
    )
    assert response.status_code == 409, response.text
    response = await client.get(
        "/records", params=list(ns.items()) + [("dg", str(ns["dg"] + 1))]
    )
    assert response.status_code == 409, response.text
    for selector in ("visitor_id", "user_id", "db_path", "database_url"):
        response = await client.post(
            "/form",
            params=ns,
            headers=h.headers(client),
            data={"body": "forbidden", selector: "1"},
        )
        error(response, "INVALID_REQUEST")
    for selector in (
        "x-demo-visitor-id",
        "x-demo-user-id",
        "x-demo-db-path",
        "x-demo-database-url",
    ):
        response = await client.get(
            "/records", params=ns, headers={selector: "1"}
        )
        error(response, "INVALID_REQUEST")
    ctx = await h.context(client, state)
    await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 0


async def security(h):
    client = await h.client()
    response = await client.get("/demo")
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert "demo_session_id=" in cookies[0] and "HttpOnly" in cookies[0]
    assert "Max-Age=28800" in cookies[0]
    assert "demo_csrftoken=" in cookies[1] and "HttpOnly" not in cookies[1]
    for cookie in cookies:
        assert (
            "Secure" in cookie
            and "SameSite=lax" in cookie
            and "Path=/" in cookie
        )
        assert "Domain=" not in cookie
    command = {"request_id": str(uuid4()), "role": "teacher"}
    for headers in (
        {"origin": "https://demo.test"},
        h.headers(client) | {"origin": "https://demo.test.evil.test"},
        h.headers(client) | {"origin": "https://demo.test:444"},
        {"x-csrf-token": client.cookies.get("demo_csrftoken")},
        {
            "x-csrf-token": client.cookies.get("demo_csrftoken"),
            "referer": "https://demo.test.evil.test/page",
        },
        h.headers(client) | {"x-csrf-token": "forged"},
    ):
        error(
            await client.post("/demo/start", json=command, headers=headers),
            "CSRF_INVALID",
        )
    response = await client.post(
        "/demo/start",
        data=command,
        headers={
            "x-csrf-token": client.cookies.get("demo_csrftoken"),
            "referer": "https://demo.test/demo",
        },
    )
    assert response.status_code == 201, response.text
    assert "set-cookie" not in response.headers
    state = response.json()
    await client.get("/demo/state")
    replay = await client.post(
        "/demo/start", json=command, headers=h.headers(client)
    )
    assert replay.status_code == 200, replay.text
    assert "set-cookie" not in replay.headers
    replay = await client.get(
        "/demo/state", params={"request_id": command["request_id"]}
    )
    assert replay.status_code == 200, replay.text
    assert "set-cookie" not in replay.headers
    for query in ("unexpected=true", "ds=x", "dg=1", "da=1"):
        error(await client.get("/demo?" + query), "INVALID_REQUEST")
    old_token = client.cookies.get("demo_csrftoken")
    response = await client.post(
        "/demo/role",
        headers=h.headers(client),
        json={
            "request_id": str(uuid4()),
            "role": "admin",
            "expected_generation": state["generation"],
            "expected_auth_epoch": state["auth_epoch"],
        },
    )
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    error(
        await client.post(
            "/demo/start", json=command, headers=h.headers(client)
        ),
        "CSRF_INVALID",
    )
    response = await client.get("/demo/state")
    assert response.status_code == 200
    assert client.cookies.get("demo_csrftoken") != old_token
    assert len(response.headers.get_list("set-cookie")) == 2


async def user_and_expiry(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    await ctx.db.execute(text('DELETE FROM "user" WHERE id=2'))
    await ctx.db.commit()
    await h.runtime.release_request_db(ctx)
    error(await client.get("/records"), "ROLE_DELETED")
    assert h.visitors.lease_count(ctx.scope) == 0
    h.clock.elapsed = 7201
    error(await client.get("/records", params=h.namespace(state)), "EXPIRED")
    assert h.visitors.lease_count(ctx.scope) == 0


async def read_reset(h):
    client = await h.client()
    state = await h.start(client)
    held = await h.context(client, state)
    await h.runtime.release_request_db(held)
    read = asyncio.create_task(client.get("/records", params={"wait": "true"}))
    await asyncio.wait_for(h.read_entered.wait(), 2)
    resetting = asyncio.create_task(h.reset(client, state))
    for _ in range(100):
        snapshot = await h.visitors.snapshot(held.verified_visitor)
        if snapshot.state != "ready":
            break
        await asyncio.sleep(0.01)
    assert snapshot.state == "resetting"
    h.read_continue.set()
    response = await read
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {
        "DEMO_STALE_GENERATION",
        "DEMO_RESET_IN_PROGRESS",
    }
    response = await resetting
    assert response.status_code == 200, response.text
    assert h.visitors.lease_count(held.scope) == 0


async def late_write(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    response = await client.post(
        "/demo/role",
        headers=h.headers(client),
        json={
            "request_id": str(uuid4()),
            "role": "admin",
            "expected_generation": state["generation"],
            "expected_auth_epoch": state["auth_epoch"],
        },
    )
    assert response.status_code == 200, response.text
    with pytest.raises(DemoError) as caught:
        await ctx.db.execute(
            text("UPDATE synthetic_record SET body='late' WHERE id=1")
        )
    assert caught.value.code == "DEMO_STALE_ROLE"
    await h.runtime.release_request_db(ctx)
    await client.get("/demo/state")
    state = response.json()
    ctx = await h.context(client, state)
    resetting = asyncio.create_task(h.reset(client, state))
    for _ in range(100):
        if (await h.visitors.snapshot(ctx.verified_visitor)).state != "ready":
            break
        await asyncio.sleep(0.01)
    with pytest.raises(DemoError):
        await ctx.db.execute(
            text("UPDATE synthetic_record SET body='late' WHERE id=1")
        )
    await h.runtime.release_request_db(ctx)
    response = await resetting
    assert response.status_code == 200, response.text


async def cleanup(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    session = ctx._request_db.session
    calls = []
    rollback, close, release = (
        session.rollback,
        session.close,
        h.visitors.release_lease,
    )

    async def observed_rollback():
        calls.append("rollback")
        return await rollback()

    async def observed_close():
        calls.append("close")
        return await close()

    async def observed_release(lease, **kwargs):
        calls.append("release")
        return await release(lease, **kwargs)

    session.rollback, session.close = observed_rollback, observed_close
    h.visitors.release_lease = observed_release
    h.runtime.handoff_request_db(ctx)
    with pytest.raises(DemoError):
        await ctx.db.commit()
    await asyncio.gather(*(h.runtime.release_request_db(ctx) for _ in range(3)))
    assert calls == ["rollback", "close", "release"], calls
    assert h.visitors.lease_count(ctx.scope) == 0
    ctx = await h.context(client, state)
    session = ctx._request_db.session
    close = session.close

    async def fail_close():
        raise RuntimeError("injected close failure")

    session.close = fail_close
    with pytest.raises(RuntimeError, match="injected close failure"):
        await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 1
    assert not ctx._request_db.released
    session.close = close
    await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 0


async def reset_order(h):
    from contextlib import asynccontextmanager

    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    await h.runtime.release_request_db(ctx)
    calls = []
    transition = h.visitors.transition
    handoff = h.runtime.handoff_request_db
    release_db = h.runtime.release_request_db
    release_lease = h.visitors.release_lease
    cancel = h.lifecycle.cancel_scope
    drain = h.lifecycle.drain
    observed_contexts = []

    @asynccontextmanager
    async def observed_transition(visitor):
        async with transition(visitor) as handle:
            mark = handle.mark_reset

            def marked(*args, **kwargs):
                calls.append("mark")
                return mark(*args, **kwargs)

            handle.mark_reset = marked
            yield handle

    def observed_handoff(context):
        calls.append("handoff")
        assert "mark" in calls
        observed_contexts.append(context)
        raw = context._request_db.session
        rollback, close = raw.rollback, raw.close

        async def rolled_back():
            assert h.visitors._states[context.visitor_id].owner is None
            calls.append("rollback")
            await rollback()

        async def closed():
            assert h.visitors._states[context.visitor_id].owner is None
            calls.append("close")
            await close()

        raw.rollback, raw.close = rolled_back, closed
        handoff(context)

    async def lease_released(lease, **kwargs):
        calls.append("release")
        return await release_lease(lease, **kwargs)

    def cancelled(scope):
        assert h.visitors._states[scope[0]].owner is None
        calls.append("cancel")
        return cancel(scope)

    async def drained(scope, seconds):
        assert h.visitors._states[scope[0]].owner is None
        calls.append("drain")
        return await drain(scope, seconds)

    h.visitors.transition = observed_transition
    h.runtime.handoff_request_db = observed_handoff
    h.visitors.release_lease = lease_released
    h.lifecycle.cancel_scope = cancelled
    h.lifecycle.drain = drained
    response = await h.reset(client, state)
    assert response.status_code == 200, response.text
    assert calls[:7] == [
        "mark",
        "handoff",
        "rollback",
        "close",
        "release",
        "cancel",
        "drain",
    ], calls
    before = list(calls)
    await release_db(observed_contexts[0])
    assert calls == before
    state = response.json()
    await client.get("/demo/state")
    held = await h.context(client, state)
    path = held.factory.engine.url.database
    response = await h.reset(client, state)
    error(response, "DRAIN_TIMEOUT")
    assert Path(unquote(urlsplit(path).path)).is_file()
    assert h.visitors.lease_count(held.scope) == 1
    await h.runtime.release_request_db(held)


async def child_context(h):
    client = await h.client()
    state = await h.start(client)
    parent = await h.context(client, state)
    async with h.lifecycle.child_context(parent) as child:
        assert child.factory is parent.factory
        assert child.lease is not parent.lease
        assert h.visitors.lease_count(parent.scope) == 2
        async with child.factory() as background_db:
            assert (
                await background_db.execute(
                    text("SELECT body FROM synthetic_record WHERE id=1")
                )
            ).scalar_one() == "fictional"
        await h.runtime.release_request_db(parent)
        assert h.visitors.lease_count(parent.scope) == 1
        reset = asyncio.create_task(h.reset(client, state))
        for _ in range(100):
            if (
                await h.visitors.snapshot(parent.verified_visitor)
            ).state != "ready":
                break
            await asyncio.sleep(0.01)
        assert child.factory is parent.factory
        assert not child.lease.released
        assert Path(
            unquote(urlsplit(child.factory.engine.url.database).path)
        ).is_file()
        with pytest.raises(DemoError):
            await h.lifecycle.assert_current(child)
    assert child.lease.released
    response = await reset
    assert response.status_code == 200, response.text
    current = await h.context(client, response.json())
    assert current.factory is not child.factory
    await h.runtime.release_request_db(current)


async def write_serialization(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    await ctx.db.execute(
        text("UPDATE synthetic_record SET body='committed' WHERE id=1")
    )
    assert ctx._request_db.handle is not None
    assert h.visitors._states[ctx.visitor_id].owner is asyncio.current_task()
    role = asyncio.create_task(
        client.post(
            "/demo/role",
            headers=h.headers(client),
            json={
                "request_id": str(uuid4()),
                "role": "admin",
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
            },
        )
    )
    await asyncio.sleep(0.05)
    assert not role.done()
    assert h.visitors._states[ctx.visitor_id].auth_epoch == state["auth_epoch"]
    await ctx.db.commit()
    assert ctx._request_db.handle is None
    response = await asyncio.wait_for(role, 2)
    assert response.status_code == 200, response.text
    await h.runtime.release_request_db(ctx)
    state = response.json()
    await client.get("/demo/state")
    ctx = await h.context(client, state)
    # ORM flush uses the same transaction guard as explicit DML.
    ctx.user.nickname = "rolled back"
    await ctx.db.flush()
    assert ctx._request_db.handle is not None
    resetting = asyncio.create_task(h.reset(client, state))
    await asyncio.sleep(0.05)
    assert not resetting.done()
    assert h.visitors._states[ctx.visitor_id].state == "ready"
    await ctx.db.rollback()
    assert ctx._request_db.handle is None
    await h.runtime.release_request_db(ctx)
    response = await asyncio.wait_for(resetting, 2)
    assert response.status_code == 200, response.text


async def reset_close_failure(h):
    client = await h.client()
    state = await h.start(client)
    context = h.runtime.context
    captured = []
    calls = []
    cancel, drain = h.lifecycle.cancel_scope, h.lifecycle.drain

    async def observed_context(request, bootstrap, generation, epoch):
        ctx = await context(request, bootstrap, generation, epoch)
        if request.url.path == "/demo/reset" and not captured:
            session = ctx._request_db.session
            captured.append((ctx, session.close))
            h.contexts.append(ctx)

            async def fail_close():
                calls.append("close")
                raise RuntimeError("injected reset close failure")

            session.close = fail_close
        return ctx

    def observed_cancel(scope):
        calls.append("cancel")
        return cancel(scope)

    async def observed_drain(scope, seconds):
        calls.append("drain")
        return await drain(scope, seconds)

    h.runtime.context = observed_context
    h.lifecycle.cancel_scope = observed_cancel
    h.lifecycle.drain = observed_drain
    try:
        response = await h.reset(client, state)
        error(response, "RESET_FAILED")
        ctx, close = captured[0]
        assert ctx._request_db.handed_off
        assert not ctx._request_db.released
        assert h.visitors.lease_count(ctx.scope) == 1
        assert Path(
            unquote(urlsplit(ctx.factory.engine.url.database).path)
        ).is_file()
        assert "close" in calls
        assert "cancel" not in calls and "drain" not in calls
        assert (
            await h.visitors.snapshot(ctx.verified_visitor)
        ).state == "reset_failed"
    finally:
        for ctx, close in captured:
            ctx._request_db.session.close = close
            await h.runtime.release_request_db(ctx)


async def restart_recovery(h):
    client = await h.client()
    state = await h.start(client)
    old_cookie = client.cookies.get("demo_session_id")
    old_nonce = h.runtime.session_signer.loads(old_cookie, max_age=28800)
    await h.session.shutdown()
    await h.visitors.shutdown()
    h.lifecycle = Lifecycle()
    h.visitors = VisitorRegistry(h.root, lifecycle=h.lifecycle, clock=h.clock)
    h.lifecycle.visitors = h.visitors
    h.runtime = deps.DemoRequestDB(
        h.visitors,
        h.lifecycle,
        seed_refs={"admin": 1, "teacher": 2},
    )
    h.session = DemoSession(
        h.visitors,
        h.lifecycle,
        h.runtime,
        tour_content=SimpleNamespace(seed_refs={"admin": 1, "teacher": 2}),
        drain_seconds=1,
    )
    h.app.state.demo_request_db = h.runtime
    h.app.state.demo_session = h.session
    assert (
        h.runtime.session_signer.loads(old_cookie, max_age=28800) == old_nonce
    )
    error(await client.get("/records/1", params=h.namespace(state)), "EXPIRED")
    error(await client.get("/demo/state"), "EXPIRED")
    error(
        await client.get("/demo/state", params={"request_id": str(uuid4())}),
        "EXPIRED",
    )
    for path, body in (
        ("/demo/start", {"request_id": str(uuid4()), "role": "teacher"}),
        (
            "/demo/role",
            {
                "request_id": str(uuid4()),
                "role": "admin",
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
            },
        ),
        (
            "/demo/reset",
            {
                "request_id": str(uuid4()),
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
                "confirm": True,
            },
        ),
    ):
        error(
            await client.post(path, json=body, headers=h.headers(client)),
            "EXPIRED",
        )
    assert not h.visitors._states
    response = await client.get("/demo")
    assert response.status_code == 200, response.text
    assert len(response.headers.get_list("set-cookie")) == 2
    new_nonce = h.runtime.session_signer.loads(
        client.cookies.get("demo_session_id"), max_age=28800
    )
    assert new_nonce != old_nonce
    assert not h.visitors._states
    csrf = client.cookies.get("demo_csrftoken")
    assert h.runtime.csrf_signer.loads(csrf, max_age=28800)[0] == new_nonce
    response = await client.get("/demo")
    assert response.status_code == 200, response.text
    assert (
        h.runtime.session_signer.loads(
            client.cookies.get("demo_session_id"), max_age=28800
        )
        == new_nonce
    )
    assert not h.visitors._states
    response = await client.post(
        "/demo/start",
        json={"request_id": str(uuid4()), "role": "teacher"},
        headers=h.headers(client),
    )
    assert response.status_code == 201, response.text
    assert "set-cookie" not in response.headers
    assert response.json()["browser_scope_id"] != state["browser_scope_id"]
    assert len(h.visitors._states) == 1
    response = await client.get("/demo")
    assert response.status_code == 200, response.text
    assert (
        h.runtime.session_signer.loads(
            client.cookies.get("demo_session_id"), max_age=28800
        )
        == new_nonce
    )
    assert len(h.visitors._states) == 1


async def malformed_form(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    await h.runtime.release_request_db(ctx)
    forged = await h.client()
    forged.cookies.set("demo_session_id", "forged")
    forged.cookies.set("demo_csrftoken", client.cookies.get("demo_csrftoken"))
    for path in ("/form", "/demo/start"):
        headers = h.headers(client) | {"content-type": "multipart/form-data"}
        error(
            await forged.post(path, content=b"malformed", headers=headers),
            "SESSION_REQUIRED",
        )
        error(
            await client.post(
                path,
                content=b"malformed",
                headers={
                    "origin": "https://demo.test",
                    "content-type": "multipart/form-data",
                },
            ),
            "CSRF_INVALID",
        )
        response = await client.post(
            path, content=b"malformed", headers=headers
        )
        error(response, "INVALID_REQUEST")
        assert response.status_code == 422, response.text
        assert h.visitors.lease_count(ctx.scope) == 0


async def rollback_failure(h):
    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    await ctx.db.execute(
        text(
            "UPDATE synthetic_record SET body='expires before commit' WHERE id=1"
        )
    )
    assert ctx._request_db.handle is not None
    h.clock.elapsed = 7201
    with pytest.raises(DemoError) as caught:
        await ctx.db.commit()
    assert caught.value.code == "DEMO_EXPIRED"
    session = ctx._request_db.session
    rollback, close, release = (
        session.rollback,
        session.close,
        h.visitors.release_lease,
    )
    calls = []

    async def observed_rollback():
        calls.append("rollback")
        await rollback()

    async def observed_close():
        calls.append("close")
        await close()

    async def observed_release(lease, **kwargs):
        calls.append("release")
        return await release(lease, **kwargs)

    session.rollback, session.close = observed_rollback, observed_close
    h.visitors.release_lease = observed_release
    try:
        with pytest.raises(DemoError) as caught:
            await h.runtime.release_request_db(ctx)
        assert caught.value.code == "DEMO_EXPIRED"
        assert calls == ["rollback", "close", "release"], calls
        assert ctx._request_db.released
        assert h.visitors.lease_count(ctx.scope) == 0
    finally:
        session.rollback, session.close = rollback, close
        h.visitors.release_lease = release
        await h.runtime.release_request_db(ctx)


async def captured_connection(h):
    from sqlalchemy.exc import ResourceClosedError

    client = await h.client()
    state = await h.start(client)
    ctx = await h.context(client, state)
    connection = await ctx.db.connection()
    assert isinstance(
        await connection.run_sync(
            lambda sync: sync.connection.driver_connection.in_transaction
        ),
        bool,
    )
    await ctx.db.execute(
        text("UPDATE synthetic_record SET body='first transaction' WHERE id=1")
    )
    await ctx.db.commit()
    connection = await ctx.db.connection()
    assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    await ctx.db.execute(
        text("UPDATE synthetic_record SET body='must roll back' WHERE id=1")
    )
    h.runtime.handoff_request_db(ctx)
    with pytest.raises(DemoError):
        await connection.execute(text("SELECT 1"))
    with pytest.raises(DemoError):
        await connection.commit()
    await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 0
    with pytest.raises((DemoError, ResourceClosedError)):
        await connection.execute(text("SELECT 1"))
    released = await h.context(client, state)
    connection = await released.db.connection()
    await h.runtime.release_request_db(released)
    with pytest.raises((DemoError, ResourceClosedError)):
        await connection.execute(text("SELECT 1"))
    response = await client.get("/records", params=h.namespace(state))
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "first transaction"
    initial = await h.context(client, state)
    connection = await initial.db.connection()
    with pytest.raises(DemoError):
        await connection.execute(
            text("UPDATE synthetic_record SET body='unguarded' WHERE id=1")
        )
    await initial.db.execute(
        text(
            "UPDATE synthetic_record SET body='initial transaction' WHERE id=1"
        )
    )
    h.runtime.handoff_request_db(initial)
    with pytest.raises(DemoError):
        await connection.execute(text("SELECT 1"))
    with pytest.raises(DemoError):
        await connection.commit()
    await h.runtime.release_request_db(initial)
    response = await client.get("/records", params=h.namespace(state))
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "first transaction"


async def typed_namespace_priority(h):
    client = await h.client()
    state = await h.start(client)
    response = await client.post(
        "/typed",
        json={"body": "typed body"},
        params=h.namespace(state),
        headers=h.headers(client),
    )
    assert response.status_code == 200, response.text
    forged = await h.client()
    forged.cookies.set("demo_session_id", "forged")
    for params, namespace_headers in (
        (h.namespace(state) | {"dg": state["generation"] + 1}, {}),
        (
            {},
            {
                "x-demo-scope": state["browser_scope_id"],
                "x-demo-generation": str(state["generation"] + 1),
                "x-demo-auth-epoch": str(state["auth_epoch"]),
            },
        ),
    ):
        headers = (
            h.headers(client)
            | namespace_headers
            | {"content-type": "application/json"}
        )
        error(
            await forged.post(
                "/typed", content=b"{", params=params, headers=headers
            ),
            "SESSION_REQUIRED",
        )
        error(
            await client.post(
                "/typed",
                content=b"{",
                params=params,
                headers=headers | {"x-csrf-token": "forged"},
            ),
            "CSRF_INVALID",
        )
        response = await client.post(
            "/typed", content=b"{", params=params, headers=headers
        )
        error(response, "STALE_GENERATION")
        assert response.status_code == 409, response.text
    error(
        await client.post(
            "/typed",
            content=b"{",
            params=h.namespace(state),
            headers=h.headers(client) | {"content-type": "application/json"},
        ),
        "INVALID_REQUEST",
    )
    ctx = await h.context(client, state)
    await h.runtime.release_request_db(ctx)
    assert h.visitors.lease_count(ctx.scope) == 0


async def control_selectors(h):
    client = await h.client()
    assert (await client.get("/demo")).status_code == 200
    forged = await h.client()
    forged.cookies.set("demo_session_id", "forged")
    command = {"request_id": str(uuid4()), "role": "teacher"}
    params = {"visitor_id": "attacker", "db_path": "/normal.db"}
    error(
        await forged.post(
            "/demo/start",
            params=params,
            json=command,
            headers=h.headers(client),
        ),
        "SESSION_REQUIRED",
    )
    error(
        await client.post(
            "/demo/start",
            params=params,
            json=command,
            headers=h.headers(client) | {"x-csrf-token": "forged"},
        ),
        "CSRF_INVALID",
    )
    assert not h.visitors._states
    for selector, value in (
        ("visitor_id", "attacker"),
        ("db_path", "/normal.db"),
        ("user_id", "1"),
        ("database_url", "sqlite+aiosqlite:////normal.db"),
    ):
        for kwargs in (
            {"params": {selector: value}, "json": command},
            {"data": command | {selector: value}},
        ):
            response = await client.post(
                "/demo/start", headers=h.headers(client), **kwargs
            )
            error(response, "INVALID_REQUEST")
            assert response.status_code == 422, response.text
            assert not h.visitors._states
    response = await client.post(
        "/demo/start",
        json=command,
        headers=h.headers(client) | {"x-demo-visitor-id": "attacker"},
    )
    error(response, "INVALID_REQUEST")
    assert not h.visitors._states


async def signer_lifecycle_matrix(h):
    client = await h.client()
    assert (await client.get("/demo")).status_code == 200
    entered, proceed = asyncio.Event(), asyncio.Event()
    clone = h.root.clone

    async def gated_clone(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await clone(*args, **kwargs)

    h.root.clone = gated_clone
    start_command = {"request_id": str(uuid4()), "role": "teacher"}
    start = asyncio.create_task(
        client.post(
            "/demo/start", json=start_command, headers=h.headers(client)
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for response in (
            await client.post(
                "/demo/start", json=start_command, headers=h.headers(client)
            ),
            await client.get(
                "/demo/state",
                params={"request_id": start_command["request_id"]},
            ),
        ):
            assert response.status_code == 202, response.text
            assert response.json()["state"] == "preparing"
            assert "set-cookie" not in response.headers
            assert response.headers["cache-control"] == "no-store"
        response = await client.get("/demo/state")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "preparing"
        assert len(response.headers.get_list("set-cookie")) == 2
    finally:
        proceed.set()
        response = await start
    assert response.status_code == 201, response.text
    assert "set-cookie" not in response.headers
    state = response.json()
    assert (await client.get("/demo/state")).status_code == 200
    entered.clear()
    proceed.clear()
    reset_id = str(uuid4())
    reset = asyncio.create_task(h.reset(client, state, reset_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for response in (
            await h.reset(client, state, reset_id),
            await client.get("/demo/state", params={"request_id": reset_id}),
        ):
            assert response.status_code == 202, response.text
            assert response.json()["state"] == "resetting"
            assert "set-cookie" not in response.headers
            assert response.headers["cache-control"] == "no-store"
        response = await client.get("/demo/state")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "resetting"
        assert len(response.headers.get_list("set-cookie")) == 2
        old_token = client.cookies.get("demo_csrftoken")
    finally:
        proceed.set()
        response = await reset
        h.root.clone = clone
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    second = response.json()
    error(await h.reset(client, state, reset_id), "CSRF_INVALID")
    response = await client.get("/demo/state")
    assert response.status_code == 200, response.text
    assert client.cookies.get("demo_csrftoken") != old_token
    response = await client.post(
        "/demo/start", json=start_command, headers=h.headers(client)
    )
    assert response.status_code == 200, response.text
    assert response.json()["historical"] is True
    assert response.json()["current"]["generation"] == second["generation"]
    assert "set-cookie" not in response.headers
    response = await h.reset(client, second)
    assert response.status_code == 200, response.text
    current = response.json()
    assert (await client.get("/demo/state")).status_code == 200
    for response in (
        await h.reset(client, state, reset_id),
        await client.get("/demo/state", params={"request_id": reset_id}),
    ):
        assert response.status_code == 200, response.text
        assert response.json()["historical"] is True
        assert response.json()["result"]["generation"] == second["generation"]
        assert response.json()["result"]["next_url"] is None
        assert response.json()["current"]["generation"] == current["generation"]
        assert response.json()["current"]["auth_epoch"] == current["auth_epoch"]
        assert "set-cookie" not in response.headers
    response = await client.get("/demo/state")
    assert response.status_code == 200, response.text
    assert response.json()["generation"] == current["generation"]
    assert response.json()["auth_epoch"] == current["auth_epoch"]
    assert response.json()["browser_scope_id"] == current["browser_scope_id"]


async def run_case(case, tmp_path):
    harness = await Harness().open(tmp_path)
    try:
        await globals()[case](harness)
    finally:
        await harness.close()


if __name__ == "__main__":
    from typing import Annotated

    from fastapi import Depends, FastAPI, Form, Request
    from fastapi.routing import APIRoute, request_response
    from httpx import ASGITransport, AsyncClient
    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.api import dependencies as deps
    from src.api.routes import demo
    from src.demo.database import DatabaseRoot, DemoError
    from src.demo.lifecycle import Lifecycle
    from src.demo.session import DemoSession
    from src.demo.visitors import VisitorRegistry

    asyncio.run(run_case(sys.argv[1], Path(sys.argv[2])))

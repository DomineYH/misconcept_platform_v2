"""Real demo app wiring: lifespan, router, errors, health, limits, templates."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SECRETS = ("s" * 48, "c" * 48, "n" * 48)


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
        DEMO_ORIGIN="https://demo.test",
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
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
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    # §4: logs carry codes and counts only, never secrets or real paths.
    assert not any(secret in output for secret in SECRETS), output
    assert str(tmp_path) not in output, output
    return result


@pytest.mark.parametrize(
    "case,extra",
    [
        ("refusal", {}),
        ("requests", {}),
        ("errors", {}),
        ("template", {}),
        (
            "health",
            {
                "DEMO_MAX_VISITORS": "1",
                "DEMO_MAX_RETIRED_GENERATIONS": "1",
                "DEMO_DRAIN_SECONDS": "1",
            },
        ),
        (
            "limits",
            {
                "DEMO_REQUESTS_PER_MINUTE": "10",
                "DEMO_STARTS_PER_IP_PER_MINUTE": "1",
            },
        ),
    ],
)
def test_demo_app(case, extra, tmp_path):
    run_case(case, tmp_path, **extra)


def test_demo_shutdown_retains_leased_files_until_restart(tmp_path):
    settings = {"DEMO_DRAIN_SECONDS": "1", "DEMO_CLEANUP_INTERVAL_SECONDS": "5"}
    run_case("shutdown", tmp_path, **settings)
    run_case("restart", tmp_path, **settings)


@pytest.mark.parametrize(
    "extra",
    [
        {"TESTING": "true"},
        {"DATABASE_URL": "sqlite+aiosqlite:///./dialogue_sim.db"},
        {"DEMO_WORKERS": "2"},
        {"DEMO_SESSION_SECRET": SECRETS[2]},
    ],
)
def test_demo_import_refuses_invalid_configuration(extra, tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DEMO_")
        and key not in {"APP_MODE", "DATABASE_URL", "TESTING"}
    }
    env.update(
        APP_MODE="demo",
        TESTING="false",
        ENV="production",
        DEMO_DATA_ROOT=str(tmp_path / "demo"),
        DEMO_ORIGIN="https://demo.test",
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
    )
    env.update(extra)
    result = subprocess.run(
        [sys.executable, "-c", "import src.main"],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "DEMO_CONFIGURATION_INVALID" in result.stderr
    assert not any(secret in result.stderr for secret in SECRETS)
    assert not (tmp_path / "demo").exists()


async def test_normal_mode_serves_no_demo_paths():
    from httpx import ASGITransport, AsyncClient
    from starlette.requests import Request

    from src.api.dependencies import templates
    from src.main import app

    assert not any(
        getattr(route, "path", "").startswith("/demo") for route in app.routes
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://normal.test"
    ) as client:
        for method, path in (
            ("GET", "/demo"),
            ("GET", "/demo/state"),
            ("POST", "/demo/start"),
            ("POST", "/demo/role"),
            ("POST", "/demo/reset"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 404, (method, path)
            assert "set-cookie" not in response.headers
    request = Request({"type": "http", "path": "/", "headers": [], "app": app})
    context = templates.TemplateResponse(
        request, "error.html", {"user": None, "status_code": 404}
    ).context
    # §24/§26: normal receives only the link value.
    assert context["demo_config"] == {"mode": "normal", "public_url": None}


# --------------------------------------------------------------------------
# Subprocess cases (APP_MODE=demo, TESTING=false, isolated environment).


async def make_seed(root_path):
    """A tiny synthetic seed, using the official explicit-engine installer."""
    import hashlib
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.db.migrations.migrate import run_all_migrations
    from src.demo.database import DatabaseRoot, DemoError, SeedSpec

    root = DatabaseRoot(root_path, initialize=True).open()
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
    db.close()

    def verify_content(db, manifest):
        if (
            db.execute(
                "SELECT body FROM synthetic_record WHERE id=1"
            ).fetchone()[0]
            != "fictional"
        ):
            raise DemoError("VERSION_MISMATCH")

    root.close()
    return SeedSpec(
        seed,
        manifest_path,
        "unit_demo_metadata.sql",
        "a" * 64,
        history,
        verify_content,
    )


def install_seed(main, spec):
    from types import SimpleNamespace

    # Test-only stand-in for #197's product SeedSpec/TourContent provider.
    main.demo_seed = lambda: (
        spec,
        SimpleNamespace(seed_refs={"admin": 1, "teacher": 2}),
    )


CLIENTS = []


def client_for(app, ip="10.0.0.1"):
    from httpx import ASGITransport, AsyncClient

    client = AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 40000)),
        base_url="https://demo.test",
    )
    CLIENTS.append(client)
    return client


def headers(client):
    return {
        "origin": "https://demo.test",
        "x-csrf-token": client.cookies.get("demo_csrftoken"),
    }


def namespace(state):
    return {
        "ds": state["browser_scope_id"],
        "dg": state["generation"],
        "da": state["auth_epoch"],
    }


def code(response):
    assert response.headers["cache-control"] == "no-store", response.headers
    return response.json()["detail"]["code"]


def normal_cookies(response):
    return [
        value
        for value in response.headers.get_list("set-cookie")
        if not value.startswith(("demo_session_id=", "demo_csrftoken="))
    ]


async def start(client, role="admin", status=201):
    from uuid import uuid4

    response = await client.get("/demo")
    assert response.status_code == 200, response.text
    response = await client.post(
        "/demo/start",
        json={"request_id": str(uuid4()), "role": role},
        headers=headers(client),
    )
    assert response.status_code == status, response.text
    if status != 201:
        return response
    assert "set-cookie" not in response.headers
    response = await client.get("/demo/state")
    assert response.status_code == 200, response.text
    return response.json()


async def wait_ready(client, generation):
    import asyncio

    for _ in range(200):
        state = (await client.get("/demo/state")).json()
        if state["state"] != "resetting":
            break
        await asyncio.sleep(0.05)
    assert state["state"] == "ready" and state["generation"] == generation
    return state


def visitor_files(root):
    return sorted(path.name for path in root.glob("*-g*-*.db"))


async def visitor_of(app, client):
    runtime = app.state.demo_request_db
    nonce = runtime.session_signer.loads(client.cookies.get("demo_session_id"))
    return runtime.visitors.visitor_for_bootstrap(
        runtime.visitors.verify_bootstrap(nonce)
    )


async def case_refusal(tmp):
    from src import main
    from src.demo.database import DatabaseRoot, DemoError

    app = main.app
    root_path = Path(os.environ["DEMO_DATA_ROOT"])

    async def refused(expected):
        try:
            async with app.router.lifespan_context(app):
                raise AssertionError("demo started")
        except DemoError as error:
            assert error.code == expected, error.code
            # A chained OSError would carry the real path into startup logs.
            assert error.__cause__ is None and error.__suppress_context__
        assert getattr(app.state, "demo_request_db", None) is None
        assert getattr(app.state, "demo_session", None) is None

    # Product startup refuses until #197's verified seed provider is bound.
    await refused("DEMO_CONFIGURATION_INVALID")
    spec = await make_seed(root_path)
    install_seed(main, spec)

    holder = DatabaseRoot(root_path).open()
    await refused("DEMO_CONFIGURATION_INVALID")  # second worker/process
    holder.close()

    manifest = spec.manifest_path.read_bytes()
    spec.manifest_path.write_bytes(manifest + b" ")
    await refused("DEMO_VERSION_MISMATCH")
    spec.manifest_path.write_bytes(manifest)

    import sqlite3

    with sqlite3.connect(spec.path) as db:
        db.execute("UPDATE synthetic_record SET body='real' WHERE id=1")
    db.close()
    await refused("DEMO_VERSION_MISMATCH")
    with sqlite3.connect(spec.path) as db:
        db.execute("UPDATE synthetic_record SET body='fictional' WHERE id=1")
    db.close()

    moved = root_path.with_name("moved")
    root_path.rename(moved)
    root_path.symlink_to(moved, target_is_directory=True)
    await refused("DEMO_CONFIGURATION_INVALID")
    root_path.unlink()
    moved.rename(root_path)

    # Refusals released the root lock; the valid root now starts.
    async with app.router.lifespan_context(app):
        response = await client_for(app).get("/health")
        assert response.status_code == 200, response.text


async def case_requests(tmp):
    from uuid import uuid4

    from src import main

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    async with app.router.lifespan_context(app):
        stranger = client_for(app, "10.0.0.9")
        response = await stranger.get("/metrics")
        assert response.status_code == 401
        assert code(response) == "DEMO_SESSION_REQUIRED"

        client = client_for(app)
        response = await client.get("/demo")
        assert response.status_code == 200
        assert not normal_cookies(response)
        state = await start(client, "admin")
        assert state["state"] == "ready" and state["role"] == "admin"

        response = await client.get("/metrics", params=namespace(state))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["database"]["users"] == 2
        assert "set-cookie" not in response.headers

        response = await client.post(
            "/demo/role",
            json={
                "request_id": str(uuid4()),
                "role": "teacher",
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
            },
            headers=headers(client),
        )
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers
        old = state
        state = (await client.get("/demo/state")).json()
        assert state["auth_epoch"] == old["auth_epoch"] + 1
        response = await client.get("/metrics", params=namespace(state))
        assert response.status_code == 403
        assert code(response) == "DEMO_ACTION_FORBIDDEN"
        response = await client.get("/metrics", params=namespace(old))
        assert code(response) == "DEMO_STALE_ROLE"

        response = await client.post(
            "/demo/reset",
            json={
                "request_id": str(uuid4()),
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
                "confirm": True,
            },
            headers=headers(client),
        )
        assert response.status_code in (200, 202), response.text
        assert not response.headers.get_list("set-cookie")
        old = state
        state = await wait_ready(client, old["generation"] + 1)
        assert state["role"] == "admin"
        response = await client.get("/metrics", params=namespace(old))
        assert code(response) == "DEMO_STALE_GENERATION"
        response = await client.get("/metrics", params=namespace(state))
        assert response.status_code == 200, response.text

        # The normal login policy is not a demo entry point.
        response = await client.post(
            "/login",
            data={"username": "fictional-admin", "password": "x" * 12},
            headers=headers(client),
        )
        assert code(response) == "DEMO_ACTION_FORBIDDEN"
        for response_client in (client, stranger):
            assert "session_id" not in response_client.cookies
            assert "csrftoken" not in response_client.cookies


async def case_errors(tmp):
    from uuid import uuid4

    from fastapi import Depends, HTTPException, Request
    from fastapi.routing import request_response

    from src import main
    from src.api import dependencies as deps

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    global_db = []
    main.AsyncSessionLocal = lambda *a, **k: global_db.append(1)

    @app.get("/probe-missing")
    async def probe_missing(
        request: Request, user=Depends(deps.get_current_user)
    ):
        if request.query_params.get("mode") == "reset":
            ctx = request.state.demo_context
            await app.state.demo_request_db.visitors.reset_visit(
                ctx, str(uuid4())
            )
        raise HTTPException(status_code=404)

    route = app.routes[-1]
    route.app = request_response(
        deps.demo_request_handler(route.get_route_handler())
    )
    html = {"accept": "text/html"}
    async with app.router.lifespan_context(app):
        stranger = client_for(app, "10.0.0.9")
        response = await stranger.get("/nope", headers=html)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert "nav-username" not in response.text
        assert "set-cookie" not in response.headers
        response = await stranger.get("/nope")
        assert response.status_code == 404
        assert code(response) == "DEMO_RESOURCE_NOT_FOUND"
        response = await stranger.get("/probe-missing", headers=html)
        assert code(response) == "DEMO_SESSION_REQUIRED"

        client = client_for(app)
        state = await start(client, "admin")
        response = await client.get(
            "/probe-missing", params=namespace(state), headers=html
        )
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert 'class="nav-username" title="Fictional admin"' in response.text
        response = await client.get("/probe-missing", params=namespace(state))
        assert code(response) == "DEMO_RESOURCE_NOT_FOUND"
        response = await client.get(
            "/probe-missing",
            params={**namespace(state), "mode": "reset"},
            headers=html,
        )
        assert response.status_code == 404
        assert "nav-username" not in response.text
        assert "Traceback" not in response.text
    assert global_db == []


async def case_template(tmp):
    from fastapi import Depends, Request
    from fastapi.routing import request_response

    from src import main
    from src.api import dependencies as deps

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))

    @app.get("/probe-template")
    async def probe_template(
        request: Request, user=Depends(deps.get_current_user)
    ):
        return deps.templates.TemplateResponse(
            request, "error.html", {"user": user, "status_code": 404}
        ).context["demo_config"]

    route = app.routes[-1]
    route.app = request_response(
        deps.demo_request_handler(route.get_route_handler())
    )
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "teacher")
        response = await client.get(
            "/probe-template",
            params=namespace(state),
            headers={"x-ui-language": "en"},
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert value == {
            "mode": "demo",
            "contract_version": "d0-03-v1-r3",
            "content_version": "d0-02-v1",
            "fixture_version": "demo-fixture-v1",
            "state": "ready",
            "browser_scope_id": state["browser_scope_id"],
            "generation": state["generation"],
            "auth_epoch": state["auth_epoch"],
            "role": "teacher",
            "expires_at": state["expires_at"],
            "idle_ttl_seconds": 1800,
            "absolute_ttl_seconds": 7200,
            "active": False,
            "last_error": None,
            "start_url": "/demo/start",
            "role_url": "/demo/role",
            "reset_url": "/demo/reset",
            "state_url": "/demo/state",
            "public_url": None,
            "csrf_cookie_name": "demo_csrftoken",
            "csrf_header_name": "x-csrf-token",
            "ui_language": "en",
            "tour_facts": None,
        }
        # A request without a current visit carries no namespace.
        request = Request(
            {"type": "http", "path": "/", "headers": [], "app": app}
        )
        value = deps.templates.TemplateResponse(
            request, "error.html", {"user": None, "status_code": 404}
        ).context["demo_config"]
        assert value["mode"] == "demo" and value["state"] is None
        assert value["browser_scope_id"] is None and value["role"] is None
        assert value["ui_language"] == "ko"
        # The error handler's legacy call style receives the same context.
        legacy = deps.templates.TemplateResponse(
            "error.html", {"request": request, "user": None, "status_code": 404}
        ).context["demo_config"]
        assert legacy == value
        assert not any(
            secret in json.dumps(value) for secret in (*SECRETS, str(tmp))
        )


async def case_health(tmp):
    import asyncio
    from uuid import uuid4

    from src import main

    app = main.app
    spec = await make_seed(Path(os.environ["DEMO_DATA_ROOT"]))
    install_seed(main, spec)
    built = len(OPENED)
    healthy = {"status": "healthy", "database": "healthy"}
    degraded = {"status": "degraded", "database": "unhealthy"}

    async def health(expected):
        response = await client_for(app, "10.0.0.8").get("/health")
        assert response.status_code == (200 if expected is healthy else 503)
        body = response.json()
        assert set(body) == {"status", "database", "uptime_seconds"}
        assert isinstance(body.pop("uptime_seconds"), (int, float))
        assert body == expected, body
        assert "set-cookie" not in response.headers

    async with app.router.lifespan_context(app):
        runtime = app.state.demo_request_db
        await health(healthy)
        client = client_for(app)
        state = await start(client, "admin")
        # §4: the visitor-count cap alone does not drop readiness.
        response = await start(client_for(app, "10.0.0.2"), status=503)
        assert code(response) == "DEMO_CAPACITY"
        await health(healthy)

        manifest = spec.manifest_path.read_bytes()
        spec.manifest_path.write_bytes(manifest + b" ")
        await health(degraded)
        spec.manifest_path.write_bytes(manifest)
        await health(healthy)

        moved = spec.path.with_name("moved.db")
        spec.path.rename(moved)
        await health(degraded)
        moved.rename(spec.path)
        await health(healthy)

        marker = runtime.visitors.root.path / ".demo-root.json"
        content = marker.read_bytes()
        marker.write_bytes(content + b" ")
        await health(degraded)
        marker.write_bytes(content)
        await health(healthy)

        # A live lease blocks the retired generation: the cleanup cap is reached.
        visitor = await visitor_of(app, client)
        ref = await runtime.visitors.resolve_visit(visitor, state["generation"])
        lease = await runtime.visitors.acquire_lease(ref, state["auth_epoch"])
        response = await client.post(
            "/demo/reset",
            json={
                "request_id": str(uuid4()),
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
                "confirm": True,
            },
            headers=headers(client),
        )
        assert response.status_code in (202, 503), response.text
        for _ in range(100):
            if (await client.get("/demo/state")).json()["state"] != "resetting":
                break
            await asyncio.sleep(0.05)
        assert runtime.visitors.pending_cleanup_count >= 1
        await health(degraded)
        await runtime.visitors.release_lease(lease)
        await runtime.visitors.cleanup_tick()
        assert runtime.visitors.pending_cleanup_count == 0
        await health(healthy)

        app.state.demo_cleanup.cancel()
        await asyncio.gather(app.state.demo_cleanup, return_exceptions=True)
        await health(degraded)
    opened = [path for path in OPENED[built:] if str(spec.path) in path]
    assert opened and all("mode=ro" in path for path in opened)


async def case_limits(tmp):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from src import main
    from src.demo.visitors import SystemClock

    class OffsetClock(SystemClock):
        offset = 0

        def monotonic(self):
            return super().monotonic() + self.offset

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    async with app.router.lifespan_context(app):
        clock = OffsetClock()
        app.state.demo_request_db.visitors.clock = clock

        a = client_for(app, "10.0.0.1")
        state_a = await start(a, "admin")
        b = client_for(app, "10.0.0.1")
        response = await start(b, status=429)
        assert code(response) == "DEMO_RATE_LIMITED"
        assert 1 <= int(response.headers["retry-after"]) <= 60
        assert response.json()["detail"]["retryable"] is True
        await start(client_for(app, "10.0.0.2"), "admin")
        spoof = client_for(app, "10.0.0.1")
        spoof.headers["x-forwarded-for"] = "10.7.7.7"
        assert code(await start(spoof, status=429)) == "DEMO_RATE_LIMITED"
        clock.offset += 61
        state_b = await start(b, "admin")

        # The per-visitor window counts this visitor's requests only.
        for _ in range(10):
            response = await a.get("/metrics", params=namespace(state_a))
            assert response.status_code == 200, response.text
        response = await a.get("/metrics", params=namespace(state_a))
        assert code(response) == "DEMO_RATE_LIMITED"
        assert 1 <= int(response.headers["retry-after"]) <= 60
        response = await b.get("/metrics", params=namespace(state_b))
        assert response.status_code == 200, response.text
        clock.offset += 61
        response = await a.get("/metrics", params=namespace(state_a))
        assert response.status_code == 200, response.text

        # Only the configured trusted proxy may supply the client address.
        proxied = ProxyHeadersMiddleware(app, trusted_hosts="10.0.0.9")
        for forwarded, status in (
            ("10.1.0.1", 201),
            ("10.1.0.2", 201),
            ("10.1.0.1", 429),
        ):
            client = client_for(proxied, "10.0.0.9")
            client.headers["x-forwarded-for"] = forwarded
            await start(client, "admin", status=status)


async def case_shutdown(tmp):
    import time

    from src import main
    from src.demo.database import DatabaseRoot

    app = main.app
    root_path = Path(os.environ["DEMO_DATA_ROOT"])
    install_seed(main, await make_seed(root_path))
    async with app.router.lifespan_context(app):
        runtime = app.state.demo_request_db
        held = client_for(app)
        state = await start(held, "teacher")
        held_files = visitor_files(root_path)
        await start(client_for(app, "10.0.0.2"), "teacher")
        assert len(visitor_files(root_path)) == 2
        visitor = await visitor_of(app, held)
        ref = await runtime.visitors.resolve_visit(visitor, state["generation"])
        await runtime.visitors.acquire_lease(ref, state["auth_epoch"])
        began = time.monotonic()
    # Bounded drain: each retired scope waits at most DEMO_DRAIN_SECONDS.
    assert time.monotonic() - began < 10
    assert visitor_files(root_path) == held_files
    DatabaseRoot(root_path).open().close()
    (tmp / "cookie.txt").write_text(held.cookies.get("demo_session_id"))


async def case_restart(tmp):
    import asyncio

    from src import main

    app = main.app
    root_path = Path(os.environ["DEMO_DATA_ROOT"])
    install_seed(main, main_spec(root_path))
    assert len(visitor_files(root_path)) == 1
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        assert visitors.pending_cleanup_count == 1
        response = await client_for(app, "10.0.0.8").get("/health")
        assert response.status_code == 200
        old = client_for(app)
        old.cookies.set(
            "demo_session_id", (tmp / "cookie.txt").read_text(), "demo.test"
        )
        assert code(await old.get("/demo/state")) == "DEMO_EXPIRED"
        assert code(await old.get("/metrics")) == "DEMO_EXPIRED"
        # The cleanup tick, not this request, removes the recovered orphan.
        for _ in range(160):
            if not visitor_files(root_path):
                break
            await asyncio.sleep(0.05)
        assert visitor_files(root_path) == []
        assert visitors.pending_cleanup_count == 0
        await start(client_for(app, "10.0.0.3"), "admin")


def main_spec(root_path):
    import sqlite3

    from src.demo.database import DemoError, SeedSpec

    seed = root_path / "seed.db"
    with sqlite3.connect(seed) as db:
        history = tuple(
            row[0]
            for row in db.execute(
                "SELECT filename FROM _migrations ORDER BY id"
            )
        )
    db.close()

    def verify_content(db, manifest):
        row = db.execute("SELECT body FROM synthetic_record WHERE id=1")
        if row.fetchone()[0] != "fictional":
            raise DemoError("VERSION_MISMATCH")

    return SeedSpec(
        seed,
        root_path / "manifest.json",
        "unit_demo_metadata.sql",
        "a" * 64,
        history,
        verify_content,
    )


OPENED = []

if __name__ == "__main__":
    import asyncio
    import io
    import logging
    import sqlite3
    from urllib.parse import unquote

    case, tmp = sys.argv[1], Path(sys.argv[2])
    connect = sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        OPENED.append(unquote(str(database)))
        return connect(database, *args, **kwargs)

    sqlite3.connect = recording_connect
    from src import main as app_main

    def forbidden(*args, **kwargs):
        raise AssertionError("normal startup path called in demo")

    app_main.init_db = app_main.close_db = forbidden
    app_main.ensure_default_admin_account = forbidden
    logs = io.StringIO()
    logging.getLogger().addHandler(logging.StreamHandler(logs))

    async def run():
        try:
            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())
    root = os.environ["DEMO_DATA_ROOT"]
    # No normal DB file is created or opened in demo.
    assert all(p == ":memory:" or p.find(root) >= 0 for p in OPENED), OPENED
    assert not any("dialogue_sim" in path for path in OPENED)
    text = logs.getvalue()
    values = [*SECRETS, root]
    for client in CLIENTS:
        values.extend(value for value in client.cookies.values() if value)
    assert not [value for value in values if value in text], text

"""Real demo app wiring: lifespan, router, errors, health, limits, templates."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SECRETS = ("s" * 48, "c" * 48, "n" * 48)


def demo_env(tmp_path, **extra):
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
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        **extra,
    )
    return env


def run_case(case, tmp_path, **extra):
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=demo_env(tmp_path, **extra),
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


def test_demo_boots_on_a_cli_built_seed(tmp_path):
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.demo.seed",
            "build",
            "--out",
            str(tmp_path / "seed"),
        ],
        env=demo_env(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    run_case("real_seed", tmp_path)


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
        DEMO_SEED_DIR=str(tmp_path / "seed"),
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


async def test_demo_cleanup_tick_retries_demo_errors_and_stops_otherwise(
    caplog,
):
    from src.demo.database import DemoError
    from src.demo.visitors import CleanupResult
    from src.main import _demo_cleanup

    calls = []

    class Visitors:
        async def cleanup_tick(self):
            calls.append(len(calls))
            if len(calls) == 1:
                raise DemoError("DB_LOCKED")
            if len(calls) == 2:
                return CleanupResult(1, 0, ())
            raise OSError("/real/demo/root/visitor.db")

    with pytest.raises(OSError):
        await _demo_cleanup(Visitors(), 0)
    # §8 retry on the next tick, recovery, then a dead task (/health → 503).
    assert len(calls) == 3
    codes = [getattr(record, "code", None) for record in caplog.records]
    assert "DEMO_DB_LOCKED" in codes and "OSError" in codes
    assert [getattr(r, "removed", None) for r in caplog.records].count(1) == 1
    assert "/real/demo/root" not in caplog.text


# --------------------------------------------------------------------------
# Subprocess cases (APP_MODE=demo, TESTING=false, isolated environment).


def boundary(monkeypatch, runtime, verify):
    """The real demo_request_handler with fake demo security and runtime."""
    from types import SimpleNamespace

    from src.api import dependencies as deps

    async def namespace(request):
        request.state.demo_invalid_selector = False

    demo = SimpleNamespace(APP_MODE="demo", DEMO_PUBLIC_URL="")
    monkeypatch.setattr(deps, "config", demo)
    monkeypatch.setattr(deps, "_demo_runtime", lambda _: runtime)
    monkeypatch.setattr(
        deps,
        "get_demo_security",
        lambda _: SimpleNamespace(verified_bootstrap=verify),
    )
    monkeypatch.setattr(deps, "_namespace", namespace)
    return deps


def boundary_request(method, path, headers=()):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": list(headers),
            "scheme": "https",
            "server": ("demo.test", 443),
        }
    )


@pytest.mark.parametrize(
    "mode",
    [
        "send",
        "no_hook",
        "finish_error",
        "release_error",
        "cancel_finish_asyncio",
        "cancel_release_asyncio",
        "cancel_finish_anyio",
    ],
)
async def test_demo_boundary_aborts_discarded_owned_response_once(
    mode, monkeypatch
):
    """#198 hook: completes exactly once even when the request is cancelled."""
    import asyncio
    from types import SimpleNamespace

    import anyio
    from starlette.responses import PlainTextResponse

    from src.demo.database import DemoError

    events = []
    entered, hold = asyncio.Event(), asyncio.Event()
    response = PlainTextResponse("owned")

    async def abort():
        events.append("abort_started")
        await anyio.lowlevel.checkpoint()
        events.append("abort_finished")

    if mode != "no_hook":
        response.demo_abort = abort

    async def finish(ctx):
        if mode == "finish_error":
            raise DemoError("STALE_GENERATION")
        if mode == "cancel_finish_anyio":
            scope.cancel()
            await anyio.lowlevel.checkpoint()
        if mode == "cancel_finish_asyncio":
            entered.set()
            await hold.wait()

    async def release(ctx):
        events.append("release")
        if mode == "release_error":
            raise RuntimeError("/real/demo/root")
        if mode == "cancel_release_asyncio":
            entered.set()
            await hold.wait()

    async def verify(request, **kwargs):
        return object()

    async def handler(request):
        request.state.demo_context = object()
        return response

    runtime = SimpleNamespace(
        visitors=SimpleNamespace(visitor_for_bootstrap=lambda _: None),
        finish=finish,
        release_request_db=release,
    )
    deps = boundary(monkeypatch, runtime, verify)
    guarded = deps.demo_request_handler(handler)
    # "/demo" skips validate_namespace but still finishes the request DB.
    request = boundary_request("GET", "/demo")
    if mode == "cancel_finish_anyio":
        # Scenario B: cancelled inside finish() by an AnyIO cancel scope.
        with anyio.CancelScope() as scope:
            await guarded(request)
    elif mode.startswith("cancel_"):
        # Scenario A (release) and a plain task cancel during finish().
        task = asyncio.create_task(guarded(request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await guarded(request)
        assert (result is response) == (mode in ("send", "no_hook"))
    expected = 0 if mode in ("send", "no_hook") else 1
    assert events.count("abort_started") == expected, events
    assert events.count("abort_finished") == expected, events
    assert events.count("release") == 1, events
    if mode in ("finish_error", "cancel_finish_asyncio", "cancel_finish_anyio"):
        # Aborted while the request DB lease is still held.
        assert events.index("abort_finished") < events.index("release"), events


async def test_demo_boundary_html_error_page_is_get_only(monkeypatch):
    from types import SimpleNamespace

    from src.demo.database import DemoError

    async def verify(request, **kwargs):
        raise DemoError("SESSION_REQUIRED")

    deps = boundary(monkeypatch, SimpleNamespace(), verify)
    guarded = deps.demo_request_handler(None)
    html = [(b"accept", b"text/html")]
    result = await guarded(boundary_request("HEAD", "/probe", html))
    assert result.status_code == 401
    assert result.headers["content-type"] == "application/json"
    result = await guarded(boundary_request("GET", "/probe", html))
    assert result.status_code == 401
    assert result.headers["content-type"].startswith("text/html")


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


def install_seed(spec, **refs):
    from types import SimpleNamespace

    seed_refs = json.loads(spec.manifest_path.read_text())["seed_refs"] | refs

    def load_seed(seed_dir):
        assert seed_dir == os.environ["DEMO_SEED_DIR"], seed_dir
        return spec, SimpleNamespace(
            seed_refs={k: v for k, v in seed_refs.items() if v is not None}
        )

    # Test-only stand-in for #197's src.demo.seed, behind the real demo_seed().
    sys.modules["src.demo.seed"] = SimpleNamespace(load_seed=load_seed)


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

    # Fail closed while #197's src.demo.seed cannot be imported.
    sys.modules["src.demo.seed"] = None
    await refused("DEMO_CONFIGURATION_INVALID")
    spec = await make_seed(root_path)
    # Tour seed_refs must equal the validated manifest's, completed_session too.
    for refs in ({"completed_session": 2}, {"completed_session": None}):
        install_seed(spec, **refs)
        await refused("DEMO_VERSION_MISMATCH")
    install_seed(spec)

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
        manifest = json.loads(spec.manifest_path.read_text())
        assert app.state.demo_request_db.seed_refs == manifest["seed_refs"]


async def case_requests(tmp):
    from uuid import uuid4

    from src import main

    app = main.app
    install_seed(await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
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

        # The normal login policy is not a demo entry point, visited or not.
        for login_client in (client, stranger):
            for path in ("/login", "/logout"):
                response = await login_client.post(
                    path,
                    data={"username": "fictional-admin", "password": "x" * 12},
                )
                assert response.status_code == 403
                assert code(response) == "DEMO_ACTION_FORBIDDEN"
                assert "set-cookie" not in response.headers
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await stranger.get(path)).status_code == 404
        from src.api.routes import (
            admin_session_actions,
            auth,
            session_analysis,
            session_messages,
            sessions,
        )

        # §8: shared-IP slowapi limits do not apply in demo.
        assert not any(
            module.limiter.enabled
            for module in (
                main,
                auth,
                sessions,
                session_messages,
                session_analysis,
                admin_session_actions,
            )
        )
        for response_client in (client, stranger):
            assert "session_id" not in response_client.cookies
            assert "csrftoken" not in response_client.cookies

        # Every visitor route crosses the request-DB boundary exactly once.
        import inspect

        from fastapi.routing import APIRoute

        from src.api.dependencies import demo_request_handler

        boundary = demo_request_handler(None).__code__
        routes = [route for route in app.routes if isinstance(route, APIRoute)]
        assert any(route.path.startswith("/demo") for route in routes)
        for route in routes:
            handler = inspect.getclosurevars(route.app).nonlocals["f"]
            depth = 0
            while hasattr(handler, "__wrapped__"):
                assert handler.__code__ is boundary, route.path
                handler, depth = handler.__wrapped__, depth + 1
            direct = route.path == "/health" or (
                route.path in ("/login", "/logout") and "POST" in route.methods
            )
            assert depth == (0 if direct else 1), (route.path, depth)


async def case_errors(tmp):
    from uuid import uuid4

    from fastapi import Depends, HTTPException, Request
    from fastapi.routing import request_response

    from src import main
    from src.api import dependencies as deps
    from src.demo.database import DemoError

    app = main.app
    install_seed(await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    global_db = []
    main.AsyncSessionLocal = lambda *a, **k: global_db.append(1)
    captured = []
    render = deps.templates.TemplateResponse

    def spy(*args, **kwargs):
        response = render(*args, **kwargs)
        captured.append(response.context)
        return response

    deps.templates.TemplateResponse = spy

    @app.get("/probe-missing")
    async def probe_missing(
        request: Request, user=Depends(deps.get_current_user)
    ):
        mode = request.query_params.get("mode")
        if mode == "reset":
            ctx = request.state.demo_context
            await app.state.demo_request_db.visitors.reset_visit(
                ctx, str(uuid4())
            )
        if mode == "stale":
            raise DemoError("STALE_GENERATION", generation=7, auth_epoch=1)
        raise HTTPException(status_code=404)

    @app.get("/probe-crash")
    async def probe_crash(user=Depends(deps.get_current_user)):
        raise RuntimeError(os.environ["DEMO_DATA_ROOT"])

    aborts = []

    @app.get("/probe-owned")
    async def probe_owned(
        request: Request, user=Depends(deps.get_current_user)
    ):
        from fastapi.responses import PlainTextResponse

        mode = request.query_params["mode"]
        # Stands in for #198's response that owns a reserved run.
        response = PlainTextResponse("owned")

        async def demo_abort():
            aborts.append(mode)
            if mode == "fail":
                raise RuntimeError(os.environ["DEMO_DATA_ROOT"])

        response.demo_abort = demo_abort
        if mode != "send":
            # Reset lands between the reservation and the boundary's finish.
            await app.state.demo_request_db.visitors.reset_visit(
                request.state.demo_context, str(uuid4())
            )
        return response

    for route in app.routes[-3:]:
        route.app = request_response(
            deps.demo_request_handler(route.get_route_handler())
        )

    def page(response, error_code, **detail):
        # §11: HTML uses the envelope's status and recovery key, without a DB.
        error = DemoError(error_code, **detail)
        assert response.status_code == error.status_code, response.text
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert "nav-username" not in response.text
        context = captured[-1]
        assert context["status_code"] == error.status_code
        assert context["demo_error"] == error.detail
        assert context["demo_error"]["recovery"].startswith("demo.")
        assert context["user"] is None
        assert context["demo_config"]["browser_scope_id"] is None
        assert context["demo_config"]["generation"] is None
        assert os.environ["DEMO_DATA_ROOT"] not in response.text

    html = {"accept": "text/html"}
    async with app.router.lifespan_context(app):
        stranger = client_for(app, "10.0.0.9")
        response = await stranger.get("/nope", headers=html)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert "nav-username" not in response.text
        assert "set-cookie" not in response.headers
        page(response, "DEMO_RESOURCE_NOT_FOUND")
        response = await stranger.get("/nope")
        assert response.status_code == 404
        assert code(response) == "DEMO_RESOURCE_NOT_FOUND"
        captured.clear()
        response = await stranger.get(
            "/probe-missing", headers={"accept": "text/html;q=0.9,*/*"}
        )
        page(response, "DEMO_SESSION_REQUIRED")
        assert captured[-1]["demo_error"]["recovery"] == "demo.start_required"
        # Non-navigations keep the JSON envelope.
        for other in (
            {},
            {"accept": "application/json"},
            {**html, "hx-request": "1"},
        ):
            response = await stranger.get("/probe-missing", headers=other)
            assert code(response) == "DEMO_SESSION_REQUIRED"
        # Errors from the /demo control routes render the same way.
        response = await stranger.get("/demo", params={"x": "1"}, headers=html)
        page(response, "DEMO_INVALID_REQUEST")
        response = await stranger.get("/demo", params={"x": "1"})
        assert code(response) == "DEMO_INVALID_REQUEST"

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
        # A DemoError raised after the user resolved names neither user nor space.
        client = client_for(app, "10.0.0.3")
        state = await start(client, "admin")
        response = await client.get(
            "/probe-missing",
            params={**namespace(state), "mode": "stale"},
            headers=html,
        )
        page(response, "DEMO_STALE_GENERATION", generation=7, auth_epoch=1)
        # §11: unknown failures are a safe envelope without exception text.
        client = client_for(app, "10.0.0.2")
        state = await start(client, "admin")
        response = await client.get("/probe-crash", params=namespace(state))
        assert response.status_code == 503
        assert code(response) == "DEMO_CONFIGURATION_INVALID"
        assert os.environ["DEMO_DATA_ROOT"] not in response.text
        response = await client.get(
            "/probe-crash", params=namespace(state), headers=html
        )
        page(response, "DEMO_CONFIGURATION_INVALID")
        assert "Traceback" not in response.text

        # A discarded owned response is aborted exactly once; a sent one never.
        client = client_for(app, "10.0.0.4")
        state = await start(client, "admin")
        response = await client.get(
            "/probe-owned", params={**namespace(state), "mode": "send"}
        )
        assert response.status_code == 200 and response.text == "owned"
        assert aborts == []
        for index, mode in enumerate(("reset", "fail")):
            client = client_for(app, f"10.0.2.{index}")
            state = await start(client, "admin")
            response = await client.get(
                "/probe-owned", params={**namespace(state), "mode": mode}
            )
            assert response.status_code >= 400, response.text
            assert code(response).startswith("DEMO_")
            # The abort error is logged by type name only (checked in __main__).
            assert os.environ["DEMO_DATA_ROOT"] not in response.text
        assert aborts == ["reset", "fail"]
    assert global_db == []


async def case_template(tmp):
    from uuid import uuid4

    from fastapi import Depends, HTTPException, Request
    from fastapi.routing import request_response

    from src import main
    from src.api import dependencies as deps
    from src.demo.session import DemoState

    app = main.app
    install_seed(await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    captured = []
    render = deps.templates.TemplateResponse

    def spy(*args, **kwargs):
        response = render(*args, **kwargs)
        captured.append(response.context["demo_config"])
        return response

    deps.templates.TemplateResponse = spy

    @app.get("/probe-template")
    async def probe_template(
        request: Request, user=Depends(deps.get_current_user)
    ):
        return deps.templates.TemplateResponse(
            request, "error.html", {"user": user, "status_code": 404}
        ).context["demo_config"]

    @app.get("/probe-error")
    async def probe_error(
        request: Request, user=Depends(deps.get_current_user)
    ):
        ctx = request.state.demo_context
        visitors = app.state.demo_request_db.visitors
        mode = request.query_params["mode"]
        if mode == "reset":
            await visitors.reset_visit(ctx, str(uuid4()))
        elif mode == "expire":
            await visitors.expire_visit(ctx.verified_visitor, "ttl")
        elif mode == "role":
            async with visitors.transition(ctx.verified_visitor) as handle:
                handle.change_role(ctx, "teacher", False)
        raise HTTPException(status_code=404)

    for route in app.routes[-2:]:
        route.app = request_response(
            deps.demo_request_handler(route.get_route_handler())
        )
    # §9:227/§24:565: every public DemoState field plus the control URLs.
    keys = set(DemoState.model_fields) | {
        "mode",
        "start_url",
        "role_url",
        "reset_url",
        "state_url",
        "public_url",
        "ui_language",
    }
    namespace_keys = (
        "state",
        "browser_scope_id",
        "generation",
        "auth_epoch",
        "role",
        "expires_at",
        "next_url",
        "provenance",
        "active",
    )

    def assert_safe(value):
        assert set(value) == keys, set(value) ^ keys
        assert value["mode"] == "demo"
        assert value["contract_version"] == "d0-03-v1-r3"
        assert value["csrf_cookie_name"] == "demo_csrftoken"
        assert value["csrf_header_name"] == "x-csrf-token"
        assert value["start_url"] == "/demo/start"
        assert value["state_url"] == "/demo/state"
        assert value["public_url"] is None and value["tour_facts"] is None
        text = json.dumps(value)
        assert not any(secret in text for secret in (*SECRETS, str(tmp)))

    def assert_unbound(value):
        assert_safe(value)
        assert all(value[key] is None for key in namespace_keys), value

    html = {"accept": "text/html"}
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
        assert_safe(value)
        # The same snapshot the state endpoint publishes for this namespace.
        assert {key: value[key] for key in state} == state
        assert value["state"] == "ready" and value["role"] == "teacher"
        assert value["provenance"]["response_source"] == "prepared_example"
        assert value["next_url"].startswith("/scenarios?ds=")
        assert value["ui_language"] == "en"

        # Error pages: only a context passing the currentness check names a namespace.
        captured.clear()
        response = await client.get(
            "/probe-error",
            params={**namespace(state), "mode": "none"},
            headers=html,
        )
        assert response.status_code == 404
        assert_safe(captured[-1])
        assert captured[-1]["generation"] == state["generation"]
        assert captured[-1]["state"] == "ready"
        for index, mode in enumerate(("reset", "expire", "role")):
            visit = client_for(app, f"10.0.1.{index}")
            visit_state = await start(visit, "admin")
            captured.clear()
            response = await visit.get(
                "/probe-error",
                params={**namespace(visit_state), "mode": mode},
                headers=html,
            )
            assert response.status_code == 404, response.text
            assert "nav-username" not in response.text
            assert len(captured) == 1
            assert_unbound(captured[0])
        captured.clear()
        response = await client.get("/nope", headers=html)
        assert response.status_code == 404
        assert_unbound(captured[0])
        assert captured[0]["ui_language"] == "ko"

        # A request without a current visit carries no namespace.
        request = Request(
            {"type": "http", "path": "/", "headers": [], "app": app}
        )
        value = render(
            request, "error.html", {"user": None, "status_code": 404}
        ).context["demo_config"]
        assert_unbound(value)
        # The error handler's legacy call style receives the same context.
        legacy = render(
            "error.html", {"request": request, "user": None, "status_code": 404}
        ).context["demo_config"]
        assert legacy == value


async def case_health(tmp):
    import asyncio
    from uuid import uuid4

    from src import main

    app = main.app
    spec = await make_seed(Path(os.environ["DEMO_DATA_ROOT"]))
    install_seed(spec)
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

        # SELECT 1 alone accepts any bytes; the seed marker must be readable.
        original = spec.path.read_bytes()
        spec.path.write_bytes(b"not a database".ljust(len(original), b"x"))
        await health(degraded)
        spec.path.write_bytes(original[:100])
        await health(degraded)
        spec.path.write_bytes(original)
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
    install_seed(await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
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
        # §11: an HTML navigation gets the same status and Retry-After.
        response = await a.get(
            "/metrics",
            params=namespace(state_a),
            headers={"accept": "text/html"},
        )
        assert response.status_code == 429
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert 1 <= int(response.headers["retry-after"]) <= 60

        async def without_csrf(client, state):
            from uuid import uuid4

            response = await client.post(
                "/demo/reset",
                json={
                    "request_id": str(uuid4()),
                    "expected_generation": state["generation"],
                    "expected_auth_epoch": state["auth_epoch"],
                    "confirm": True,
                },
                headers={"origin": "https://demo.test"},
            )
            return code(response)

        async def selector(client, state):
            response = await client.get(
                "/metrics", params={**namespace(state), "visitor_id": "x"}
            )
            return code(response)

        # Exhausted window: CSRF and selector errors still take precedence.
        assert await without_csrf(a, state_a) == "DEMO_CSRF_INVALID"
        assert await selector(a, state_a) == "DEMO_INVALID_REQUEST"
        # Shared IP, other visitor; rejected requests consume none of its quota.
        for _ in range(3):
            assert await without_csrf(b, state_b) == "DEMO_CSRF_INVALID"
            assert await selector(b, state_b) == "DEMO_INVALID_REQUEST"
        for _ in range(9):
            response = await b.get("/metrics", params=namespace(state_b))
            assert response.status_code == 200, response.text
        response = await b.get("/metrics", params=namespace(state_b))
        assert code(response) == "DEMO_RATE_LIMITED"
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


async def case_real_seed(tmp):
    import sqlite3

    from src import main
    from src.demo import seed
    from src.demo.database import DemoError

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    seed_dir = Path(os.environ["DEMO_SEED_DIR"])
    manifest = json.loads(seed.MANIFEST.read_text())
    async with app.router.lifespan_context(app):
        # No stand-in: demo_seed() -> load_seed(DEMO_SEED_DIR) -> validate_seed.
        assert app.state.demo_seed.path == seed_dir / seed.SEED_NAME
        response = await client_for(app, "10.0.0.9").get("/health")
        assert response.status_code == 200, response.text
        refs = app.state.demo_request_db.seed_refs
        assert refs == manifest["seed_refs"] and "completed_session" in refs
        assert dict(app.state.demo_session.tour_content.seed_refs) == refs

        a, b = client_for(app, "10.0.0.1"), client_for(app, "10.0.0.2")
        state_a, state_b = await start(a, "admin"), await start(b, "admin")
        assert state_a["browser_scope_id"] != state_b["browser_scope_id"]
        assert len(visitor_files(root)) == 2
        counts = [
            (await client.get("/metrics", params=namespace(state))).json()
            for client, state in ((a, state_a), (b, state_b))
        ]
        assert counts[0]["database"] == counts[1]["database"]
        assert counts[0]["database"]["users"] > 0
        ns = namespace(state_a)
        response = await a.post(
            "/admin/groups",
            json={"name": "Visitor A only"},
            headers=headers(a)
            | {
                "x-demo-scope": ns["ds"],
                "x-demo-generation": str(ns["dg"]),
                "x-demo-auth-epoch": str(ns["da"]),
            },
        )
        assert response.status_code == 201, response.text
        pages = [
            await client.get(
                "/admin/groups",
                params=namespace(state),
                headers={"accept": "text/html"},
            )
            for client, state in ((a, state_a), (b, state_b))
        ]
        assert all(page.status_code == 200 for page in pages), pages[1].text
        assert "Visitor A only" in pages[0].text
        assert "Visitor A only" not in pages[1].text

    # A tampered seed refuses startup and binds nothing.
    with sqlite3.connect(seed_dir / seed.SEED_NAME) as db:
        db.execute(
            "UPDATE \"user\" SET nickname = nickname || '!' WHERE id = 1"
        )
    db.close()
    try:
        async with app.router.lifespan_context(app):
            raise AssertionError("tampered seed started")
    except DemoError as error:
        assert error.code == "DEMO_VERSION_MISMATCH", error.code
    assert getattr(app.state, "demo_request_db", None) is None


async def case_shutdown(tmp):
    import time

    from src import main
    from src.demo.database import DatabaseRoot

    app = main.app
    root_path = Path(os.environ["DEMO_DATA_ROOT"])
    install_seed(await make_seed(root_path))
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
    install_seed(main_spec(root_path))
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
    # No normal DB file is created or opened in demo; only root and seed dir.
    assert all(
        p == ":memory:"
        or p.find(root) >= 0
        or p.find(os.environ["DEMO_SEED_DIR"]) >= 0
        for p in OPENED
    ), OPENED
    assert not any("dialogue_sim" in path for path in OPENED)
    text = logs.getvalue()
    values = [*SECRETS, root]
    for client in CLIENTS:
        values.extend(value for value in client.cookies.values() if value)
    assert not [value for value in values if value in text], text

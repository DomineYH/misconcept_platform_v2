"""#203 D4-01 security: config refusal, mode switching, CSRF/cookies, egress.

Demo subprocess cases reuse the seed/client helpers of test_demo_d1_04_app and
run THIS file as the script (one process per group to keep boot costs low).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_demo_d1_04_app import (
    CLIENTS,
    SECRETS,
    client_for,
    code,
    headers,
    install_seed,
    make_seed,
    namespace,
    start,
    visitor_files,
    wait_ready,
)
from test_provider_connections import api as provider_api

api = provider_api  # fixture re-export, as test_model_management does
ROOT = Path(__file__).resolve().parents[1]
DEMO = "https://demo.test"
CLEAN_PREFIXES = (
    "DEMO_",
    "ANTHROPIC_",
    "OPENAI_",
    "AZURE_",
    "GOOGLE_",
    "GEMINI_",
)
CLEAN_NAMES = {
    "APP_MODE",
    "DATABASE_URL",
    "TESTING",
    "SESSION_SECRET",
    "ENV",
    "BOOTSTRAP_ADMIN_ON_STARTUP",
    "PROVIDER_SECRET_ENCRYPTION_KEY",
    "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
}


def demo_env(tmp_path, **extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(CLEAN_PREFIXES) and k not in CLEAN_NAMES
    }
    env.update(
        APP_MODE="demo",
        TESTING="false",
        ENV="production",
        DEMO_DATA_ROOT=str(tmp_path / "demo"),
        DEMO_ORIGIN=DEMO,
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
        PYTHONPATH=str(ROOT),
    )
    env.update(extra)
    return env


def run_case(case, tmp_path):
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=demo_env(tmp_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not any(secret in output for secret in SECRETS), output


def run_import(tmp_path, code_text, *, cwd=ROOT, **extra):
    return subprocess.run(
        [sys.executable, "-c", code_text],
        env=demo_env(tmp_path, **extra),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------
# §2 configuration refusal (the four basics live in test_demo_d1_04_app).

INJECTED = "sk-injected-value-9f3a"
FORBIDDEN = {
    **dict.fromkeys(
        (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "PROVIDER_SECRET_ENCRYPTION_KEY",
        ),
        INJECTED,
    ),
    **dict.fromkeys(
        (
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
            "AZURE_OPENAI_ENDPOINT",
            "GOOGLE_GEMINI_BASE_URL",
        ),
        "https://" + INJECTED + ".example.net",
    ),
    "BOOTSTRAP_ADMIN_ON_STARTUP": "true",
}
# Each boot costs seconds: every name is checked against Config() in one
# process; three representatives also go through a real `import src.main`.
LOOP = """
import os, traceback
from src.config import Config
from src.demo.database import DemoError
for name, value in %r.items():
    os.environ[name] = value
    try:
        Config()
    except DemoError as error:
        assert error.code == "DEMO_CONFIGURATION_INVALID", name
        assert %r not in "".join(traceback.format_exception(error)), name
    else:
        raise AssertionError(name + " accepted")
    del os.environ[name]
"""


@pytest.mark.parametrize("value", ["Demo", "", "production"])
def test_demo_import_refuses_invalid_app_mode(value, tmp_path):
    result = run_import(tmp_path, "import src.main", APP_MODE=value)
    assert result.returncode != 0
    assert "DEMO_CONFIGURATION_INVALID" in result.stderr
    assert not (tmp_path / "demo").exists()


def test_demo_config_refuses_every_forbidden_input(tmp_path):
    result = run_import(tmp_path, LOOP % (FORBIDDEN, INJECTED))
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "demo").exists()


@pytest.mark.parametrize(
    "name",
    ["OPENAI_API_KEY", "PROVIDER_SECRET_ENCRYPTION_KEY", "ANTHROPIC_BASE_URL"],
)
def test_demo_import_refuses_forbidden_input(name, tmp_path):
    result = run_import(tmp_path, "import src.main", **{name: FORBIDDEN[name]})
    assert result.returncode != 0
    assert "DEMO_CONFIGURATION_INVALID" in result.stderr
    assert INJECTED not in result.stderr
    assert not (tmp_path / "demo").exists()


def test_demo_import_refuses_bootstrap_admin(tmp_path):
    result = run_import(
        tmp_path, "import src.main", BOOTSTRAP_ADMIN_ON_STARTUP="true"
    )
    assert result.returncode != 0
    assert "DEMO_CONFIGURATION_INVALID" in result.stderr
    assert not (tmp_path / "demo").exists()


def test_dotenv_is_ignored_in_demo_and_read_in_normal(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    probe = "from src.config import config; print(config.OPENAI_API_KEY)"
    demo = run_import(tmp_path, probe, cwd=tmp_path)
    # §2: ignored, not refused, and never loaded into settings.
    assert demo.returncode == 0, demo.stderr
    assert demo.stdout.strip() == ""
    normal = run_import(
        tmp_path,
        probe,
        cwd=tmp_path,
        APP_MODE="normal",
        SESSION_SECRET="n" * 48,
    )
    assert normal.returncode == 0, normal.stderr
    assert normal.stdout.strip() == "sk-from-dotenv"


# --------------------------------------------------------------------------
# §2/§9 normal mode never honours client-side mode switches.

SWITCHES = [
    {"params": {"app_mode": "demo"}},
    {"headers": {"x-app-mode": "demo"}},
    {"headers": {"cookie": "demo_session_id=x; demo_csrftoken=y"}},
]


async def test_normal_mode_ignores_client_mode_switches():
    from httpx import ASGITransport, AsyncClient

    from src.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://normal.test"
    ) as client:
        for switch in SWITCHES:
            for path in ("/demo", "/demo/state", "/demo/start"):
                for method in ("GET", "POST"):
                    response = await client.request(method, path, **switch)
                    assert response.status_code == 404, (method, path, switch)
                    assert "set-cookie" not in response.headers
            for path in ("/demo/role", "/demo/reset"):
                response = await client.post(path, **switch)
                assert response.status_code == 404, (path, switch)
                assert "set-cookie" not in response.headers
        # Demo cookies do not authenticate (or change) a protected route.
        plain = await client.get("/admin/users", follow_redirects=False)
        for switch in SWITCHES:
            other = await client.get(
                "/admin/users", follow_redirects=False, **switch
            )
            assert other.status_code == plain.status_code
            assert other.headers.get("location") == plain.headers.get(
                "location"
            )
            assert other.headers.get_list(
                "set-cookie"
            ) == plain.headers.get_list("set-cookie")
        assert plain.status_code in (302, 303, 307, 401)


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_normal_admin_api_rejects_the_demo_provider(data, api):
    """§13/§20: the local provider never exists in normal; no row is written."""
    csrf = {"x-csrf-token": api.cookies["csrftoken"]}
    before = (await api.get("/admin/ai/state")).json()["models"]
    for path, body in (
        (
            "/admin/ai/models",
            dict(
                provider="demo", model_id="demo-classroom-v1", display_name="D"
            ),
        ),
        (
            "/admin/ai/providers/demo/key",
            dict(
                expected_version=1, current_password="x" * 12, api_key="k" * 12
            ),
        ),
    ):
        response = await api.post(path, json=body, headers=csrf)
        assert response.status_code == 422, (path, response.text)
    assert (await api.get("/admin/ai/state")).json()["models"] == before


# --------------------------------------------------------------------------
# Demo subprocess tests: one process per group.


def test_demo_csrf_origin_cookie_matrix(tmp_path):
    run_case("csrf", tmp_path)


def test_demo_cross_auth_and_role_deleted(tmp_path):
    run_case("auth", tmp_path)


def test_demo_lifecycle_makes_no_external_calls(tmp_path):
    run_case("egress", tmp_path)


@pytest.mark.skip(
    reason="needs #200: §20 demo admin guards are not merged "
    "(main: key/catalog/settings/comparisons 422 by body validation, "
    "POST /admin/ai/models registers a model with 200)"
)
def test_demo_admin_dangerous_endpoints_are_forbidden(tmp_path):
    run_case("dangerous", tmp_path)


@pytest.mark.skip(
    reason="needs G4 / #198: egress 0 across student/mentor/analysis/retry/legacy runs"
)
def test_demo_runs_make_no_external_calls():
    """§14/§28: every run kind in demo leaves egress/provider-call counts at 0."""
    ...


@pytest.mark.skip(
    reason="needs G4 / #198: demo model selection guard on scenario API/snapshot"
)
def test_demo_rejects_injected_external_model():
    """§20: injecting an external model through scenario API/snapshot -> 422
    DEMO_UNSUPPORTED_CONFIGURATION in demo, with no catalog/probe/ledger rows.
    """
    ...


# --------------------------------------------------------------------------
# Subprocess cases (APP_MODE=demo, TESTING=false, isolated environment).

ATTEMPTS = []


def install_egress_guard():
    """§14: record (and refuse) every non-loopback socket or SDK client use."""
    import socket

    import httpx

    local = ("127.0.0.1", "::1", "localhost", "", None)

    def host_of(address):
        # AF_UNIX addresses are str/bytes; AF_INET[6] are tuples.
        return address[0] if isinstance(address, tuple) else None

    def record(kind, host):
        if host not in local:
            ATTEMPTS.append((kind, str(host)))
            raise OSError("egress blocked")

    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)

        def guarded(self, address, _name=name, _original=original):
            record(_name, host_of(address))
            return _original(self, address)

        setattr(socket.socket, name, guarded)
    create = socket.create_connection
    socket.create_connection = lambda address, *a, **k: (
        record("create_connection", host_of(address))
        or create(address, *a, **k)
    )
    lookup = socket.getaddrinfo
    socket.getaddrinfo = lambda host, *a, **k: (
        record("getaddrinfo", host) or lookup(host, *a, **k)
    )

    def refuse(kind):
        def patched(self, *a, **k):
            ATTEMPTS.append((kind, "client"))
            raise OSError("egress blocked")

        return patched

    httpx.HTTPTransport.handle_request = refuse("httpx")
    httpx.AsyncHTTPTransport.handle_async_request = refuse("httpx-async")
    for module, name in (
        ("openai", "AsyncOpenAI"),
        ("openai", "OpenAI"),
        ("anthropic", "AsyncAnthropic"),
        ("anthropic", "Anthropic"),
        ("google.genai", "Client"),
    ):
        try:
            cls = getattr(__import__(module, fromlist=[name]), name)
        except (ImportError, AttributeError):
            continue
        cls.__init__ = refuse(module + "." + name)


async def prove_egress_guard():
    """The guard must catch real attempts, else a zero count proves nothing."""
    import socket

    import httpx
    from openai import AsyncOpenAI

    for attempt in (
        lambda: socket.create_connection(("203.0.113.9", 9), timeout=0.1),
        lambda: socket.socket().connect(("203.0.113.9", 9)),
        lambda: socket.getaddrinfo("provider.invalid", 443),
        lambda: AsyncOpenAI(api_key="x"),
    ):
        with pytest.raises(OSError):
            attempt()
    with pytest.raises(OSError):
        await httpx.AsyncClient().get("https://provider.invalid/")
    assert len(ATTEMPTS) == 5, ATTEMPTS
    ATTEMPTS.clear()


def parse_cookie(response, name):
    """Return the Set-Cookie attributes of one cookie, lower-cased keys."""
    (raw,) = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(name + "=")
    ]
    parts = [part.strip() for part in raw.split(";")]
    return {
        key.lower(): value
        for key, _, value in (part.partition("=") for part in parts[1:])
    }


def assert_demo_cookies(response):
    session = parse_cookie(response, "demo_session_id")
    assert session == {
        "path": "/",
        "max-age": "28800",
        "httponly": "",
        "samesite": "lax",
        "secure": "",
    }, session
    # CSRF cookie: readable by script, session-scoped (no Max-Age/Expires).
    assert parse_cookie(response, "demo_csrftoken") == {
        "path": "/",
        "samesite": "lax",
        "secure": "",
    }


def assert_no_cookie(response):
    assert not response.headers.get_list("set-cookie"), response.headers


def snapshot(state):
    return {k: state[k] for k in ("state", "generation", "auth_epoch", "role")}


def rejected_headers(client, other):
    token = client.cookies.get("demo_csrftoken")
    session = client.cookies.get("demo_session_id")
    foreign = other.cookies.get("demo_csrftoken")
    return {
        "missing token": {"origin": DEMO},
        "wrong token": {"origin": DEMO, "x-csrf-token": "wrong"},
        "other client's token": {"origin": DEMO, "x-csrf-token": foreign},
        "other client's token in cookie too": {
            "origin": DEMO,
            "x-csrf-token": foreign,
            "cookie": f"demo_session_id={session}; demo_csrftoken={foreign}",
        },
        "normal origin": {
            "origin": "https://normal.test",
            "x-csrf-token": token,
        },
        "evil origin": {"origin": "https://evil.test", "x-csrf-token": token},
        "no origin, no referer": {"x-csrf-token": token},
        "no origin, cross-origin referer": {
            "referer": "https://evil.test/demo",
            "x-csrf-token": token,
        },
    }


async def assert_csrf_rejected(client, other, path, body, probe):
    """Every bad CSRF/Origin variant: 403, no cookie, no state change (§9.1)."""
    before = await probe()
    for name, hdrs in rejected_headers(client, other).items():
        response = await client.post(path, json=body, headers=hdrs)
        assert response.status_code == 403, (path, name, response.text)
        assert code(response) == "DEMO_CSRF_INVALID", (path, name)
        assert_no_cookie(response)
        assert await probe() == before, (path, name)
    # A form without Origin and with a cross-origin / absent Referer is refused too.
    for referer in ({"referer": "https://evil.test/demo"}, {}):
        response = await client.post(
            path,
            data={k: str(v).lower() for k, v in body.items()},
            headers={
                "x-csrf-token": client.cookies.get("demo_csrftoken"),
                **referer,
            },
        )
        assert response.status_code == 403, (path, referer, response.text)
        assert code(response) == "DEMO_CSRF_INVALID"
    assert await probe() == before


async def case_csrf(tmp):
    from src import main

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    install_seed(main, await make_seed(root))
    async with app.router.lifespan_context(app):
        a, b = client_for(app, "10.0.0.1"), client_for(app, "10.0.0.2")
        # §10 cookie attributes on the bootstrap response.
        response = await a.get("/demo")
        assert response.status_code == 200
        assert_demo_cookies(response)
        assert not [
            c
            for c in response.headers.get_list("set-cookie")
            if not c.startswith(("demo_session_id=", "demo_csrftoken="))
        ]
        await b.get("/demo")

        async def probe():
            response = await a.get("/demo/state")
            if response.status_code != 200:
                return response.status_code, code(response), visitor_files(root)
            return snapshot(response.json()), visitor_files(root)

        # Fresh bootstrap /demo/start: nothing is created by a rejected CSRF.
        start_body = {"request_id": str(uuid4()), "role": "admin"}
        await assert_csrf_rejected(a, b, "/demo/start", start_body, probe)
        assert visitor_files(root) == []
        # No Origin + same-origin Referer is accepted for a form (§10).
        response = await a.post(
            "/demo/start",
            data=start_body,
            headers={
                "referer": DEMO + "/demo",
                "x-csrf-token": a.cookies.get("demo_csrftoken"),
            },
        )
        assert response.status_code == 201, response.text
        assert_no_cookie(response)  # first completion (§9.1 N)
        state = (await a.get("/demo/state")).json()
        assert state["state"] == "ready" and state["role"] == "admin"

        # A data-route mutation needs the same CSRF proof; the teacher survives.
        delete = {"params": namespace(state)}
        response = await a.post(
            "/admin/users/2/delete", headers={"origin": DEMO}, **delete
        )
        assert (
            response.status_code == 403
            and code(response) == "DEMO_CSRF_INVALID"
        )
        response = await a.post(
            "/admin/users/2/delete",
            headers={"origin": DEMO, "x-csrf-token": "wrong"},
            **delete,
        )
        assert code(response) == "DEMO_CSRF_INVALID"
        assert_no_cookie(response)
        users = (await a.get("/metrics", params=namespace(state))).json()
        assert users["database"]["users"] == 2

        role = {
            "request_id": str(uuid4()),
            "role": "teacher",
            "expected_generation": state["generation"],
            "expected_auth_epoch": state["auth_epoch"],
        }
        reset = {
            "request_id": str(uuid4()),
            "expected_generation": state["generation"],
            "expected_auth_epoch": state["auth_epoch"],
            "confirm": True,
        }
        for path, body in (("/demo/role", role), ("/demo/reset", reset)):
            await assert_csrf_rejected(a, b, path, body, probe)

        # Pre-rotation token: valid before the role change, dead after the
        # next current-state GET rotates the revision.
        old = a.cookies.get("demo_csrftoken")
        response = await a.post("/demo/role", json=role, headers=headers(a))
        assert response.status_code == 200, response.text
        assert_no_cookie(response)
        response = await a.get("/demo/state")  # C: rotates both cookies
        assert_demo_cookies(response)
        rotated = response.json()
        assert rotated["role"] == "teacher"
        assert a.cookies.get("demo_csrftoken") != old
        session = a.cookies.get("demo_session_id")
        stale = {
            "origin": DEMO,
            "x-csrf-token": old,
            "cookie": f"demo_session_id={session}; demo_csrftoken={old}",
        }
        jar_mismatch = {"origin": DEMO, "x-csrf-token": old}
        later_role = {**role, "request_id": str(uuid4()), "role": "admin"}
        later_role["expected_auth_epoch"] = rotated["auth_epoch"]
        later_reset = {**reset, "request_id": str(uuid4())}
        later_reset["expected_auth_epoch"] = rotated["auth_epoch"]
        for path, body in (
            ("/demo/role", later_role),
            ("/demo/reset", later_reset),
        ):
            for hdrs in (stale, jar_mismatch):
                response = await a.post(path, json=body, headers=hdrs)
                assert response.status_code == 403, (path, response.text)
                assert code(response) == "DEMO_CSRF_INVALID"
                assert_no_cookie(response)
        assert snapshot((await a.get("/demo/state")).json()) == snapshot(
            rotated
        )

        # §9.1 N: same-ID replays, historical GETs and errors carry no cookie.
        response = await a.post("/demo/role", json=role, headers=headers(a))
        assert response.status_code == 200
        assert_no_cookie(response)
        response = await a.get(
            "/demo/state", params={"request_id": role["request_id"]}
        )
        assert response.status_code == 200, response.text
        assert_no_cookie(response)
        response = await a.get("/demo/state", params={"bogus": "1"})
        assert response.status_code >= 400
        assert_no_cookie(response)
        response = await a.post(
            "/demo/reset", json=later_reset, headers=headers(a)
        )
        assert response.status_code in (200, 202), response.text
        assert_no_cookie(response)
        await wait_ready(a, rotated["generation"] + 1)
        await a.get("/demo/state")  # current CSRF revision for the replay
        response = await a.post(
            "/demo/reset", json=later_reset, headers=headers(a)
        )
        assert response.status_code in (200, 202)
        assert_no_cookie(response)
        response = await a.get("/demo/state")
        assert_demo_cookies(response)
        assert a.cookies.get("demo_csrftoken")


async def keep_real_tables(spec, tmp):
    """Swap make_seed's synthetic session tables for the migrated ones.

    The shared stand-in omits columns the real delete routes query (teacher_id
    ...); #197's product seed keeps them. Removal when #197 lands.
    """
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.db.migrations.migrate import run_all_migrations

    tables = (
        "session_summary",
        "session_feedback_report",
        "generation_run",
        "message",
        "session",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'real.db'}")
    await run_all_migrations(db_engine=engine)
    await engine.dispose()
    marks = ",".join("?" * len(tables))
    with (
        sqlite3.connect(tmp / "real.db") as real,
        sqlite3.connect(spec.path) as db,
    ):
        ddl = [
            row[0]
            for row in real.execute(
                f"SELECT sql FROM sqlite_master WHERE tbl_name IN ({marks}) "
                "AND sql IS NOT NULL ORDER BY type DESC, rowid",
                tables,
            )
        ]
        for table in tables:
            db.execute(f'DROP TABLE "{table}"')
        for statement in ddl:
            db.execute(statement)
    real.close(), db.close()


async def case_auth(tmp):
    import base64

    from itsdangerous import TimestampSigner, URLSafeTimedSerializer

    from src import main

    app = main.app
    spec = await make_seed(Path(os.environ["DEMO_DATA_ROOT"]))
    await keep_real_tables(spec, tmp)
    # The delete route's tour-fact capture needs the full seed_refs set that
    # the shared stand-in omits (#197 supplies the product one).
    refs = dict(admin=1, teacher=2, group=1, scenario=1, completed_session=1)
    main.demo_seed = lambda: (
        spec,
        SimpleNamespace(seed_refs=refs, supported_snapshot=lambda row: False),
    )
    nonce = "p" * 40
    # A normal-service login cookie for user 1 (the admin) under SESSION_SECRET.
    payload = base64.b64encode(json.dumps({"user_id": 1}).encode())
    normal = TimestampSigner(SECRETS[2]).sign(payload).decode()
    async with app.router.lifespan_context(app):
        stranger = client_for(app, "10.0.0.9")
        await stranger.get("/demo")
        forged = {
            "random": "x" * 40,
            "normal key": URLSafeTimedSerializer(
                SECRETS[2], salt="demo-session-v1"
            ).dumps(nonce),
            "csrf key": URLSafeTimedSerializer(
                SECRETS[1], salt="demo-session-v1"
            ).dumps(nonce),
            "csrf token as session": stranger.cookies.get("demo_csrftoken"),
            "session key, csrf salt": URLSafeTimedSerializer(
                SECRETS[0], salt="demo-csrf-v1"
            ).dumps(nonce),
        }
        for name, value in forged.items():
            for path in ("/metrics", "/admin/users"):
                response = await client_for(app, "10.0.0.9").get(
                    path,
                    params={"ds": "x", "dg": 1, "da": 1},
                    headers={"cookie": f"demo_session_id={value}"},
                )
                assert response.status_code == 401, (name, path, response.text)
                assert code(response) == "DEMO_SESSION_REQUIRED", (name, path)
                assert (
                    "database" not in response.text
                    and "users" not in response.text
                )
        response = await client_for(app, "10.0.0.9").get(
            "/metrics", headers={"cookie": f"session_id={normal}"}
        )
        assert (
            response.status_code == 401
            and code(response) == "DEMO_SESSION_REQUIRED"
        )

        # A valid teacher visit is not elevated by a normal admin login cookie.
        teacher = client_for(app, "10.0.0.2")
        state = await start(teacher, "teacher")
        cookies = "; ".join(
            f"{name}={teacher.cookies.get(name)}"
            for name in ("demo_session_id", "demo_csrftoken")
        )
        response = await teacher.get(
            "/metrics",
            params=namespace(state),
            headers={"cookie": f"{cookies}; session_id={normal}"},
        )
        assert response.status_code == 403
        assert code(response) == "DEMO_ACTION_FORBIDDEN"

        # §9/§22: deleting the chosen role's seed account blocks a switch to it.
        admin = client_for(app, "10.0.0.3")
        state = await start(admin, "admin")

        async def counts():
            return await admin.get("/metrics", params=namespace(state))

        response = await admin.post(
            "/admin/users/1/delete",
            params=namespace(state),
            headers=headers(admin),
        )
        assert response.status_code >= 400, response.text
        assert (await counts()).json()["database"]["users"] == 2
        response = await admin.post(
            "/admin/users/2/delete",
            params=namespace(state),
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        assert (await counts()).json()["database"]["users"] == 1
        await admin.get("/demo/state")
        response = await admin.post(
            "/demo/role",
            json={
                "request_id": str(uuid4()),
                "role": "teacher",
                "expected_generation": state["generation"],
                "expected_auth_epoch": state["auth_epoch"],
            },
            headers=headers(admin),
        )
        assert response.status_code == 409, response.text
        assert code(response) == "DEMO_ROLE_DELETED"


DANGEROUS = (
    (
        "/admin/ai/providers/openai/key",
        {
            "expected_version": 1,
            "current_password": "x" * 12,
            "api_key": "sk-demo-key-123456",
        },
    ),
    ("/admin/ai/providers/openai/catalog", {"expected_version": 1}),
    (
        "/admin/ai/models",
        {
            "provider": "openai",
            "model_id": "gpt-5.5",
            "display_name": "Injected",
        },
    ),
    ("/admin/ai/settings/update", {"expected_version": 1}),
    ("/admin/comparisons", {}),
    ("/admin/comparison-conversations", {}),
)


async def case_dangerous(tmp):
    from src import main

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    async with app.router.lifespan_context(app):
        admin = client_for(app)
        state = await start(admin, "admin")
        failures = []
        for path, body in DANGEROUS:
            response = await admin.post(
                path, json=body, params=namespace(state), headers=headers(admin)
            )
            if (
                response.status_code != 403
                or response.json().get("detail", {}).get("code")
                != "DEMO_ACTION_FORBIDDEN"
            ):
                failures.append(
                    (path, response.status_code, response.text[:120])
                )
        print("DANGEROUS", failures)
        assert not failures, failures


async def case_egress(tmp):
    from src import main

    app = main.app
    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    async with app.router.lifespan_context(app):
        runtime = app.state.demo_request_db
        client = client_for(app)
        state = await start(client, "admin")
        assert (await client.get("/health")).status_code == 200
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
        state = (await client.get("/demo/state")).json()
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
        await wait_ready(client, state["generation"] + 1)
        admin = client_for(app, "10.0.0.2")
        state = await start(admin, "admin")
        for path, body in DANGEROUS:
            # Statuses are case_dangerous's concern; here only egress counts.
            await admin.post(
                path, json=body, params=namespace(state), headers=headers(admin)
            )
        await runtime.visitors.cleanup_tick()
        assert (await client.get("/health")).status_code == 200
    # Shutdown ran inside the guard too; the module epilogue asserts zero.


if __name__ == "__main__":
    import asyncio

    case, tmp = sys.argv[1], Path(sys.argv[2])
    install_egress_guard()
    from src import main as app_main  # noqa: F401

    async def run():
        try:
            await prove_egress_guard()
            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())
    # Whatever the case did, nothing reached a socket or an SDK client.
    assert ATTEMPTS == [], ATTEMPTS

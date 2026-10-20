"""#203 D4-01 security: config refusal, mode switching, CSRF/cookies, egress.

Demo subprocess cases boot the real app on the product seed (built by the
#197 CLI), reuse the client helpers of test_demo_d1_04_app and run THIS file
as the script (one process per group to keep boot costs low).
"""

import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest
from test_demo_d1_04_app import (
    CLIENTS,
    SECRETS,
    client_for,
    code,
    headers,
    namespace,
    start,
    visitor_files,
    wait_ready,
)

if __name__ != "__main__":
    # Imports src.main; as a script it must wait for the egress guard.
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
        DEMO_SEED_DIR=str(tmp_path / "seed"),
        DEMO_ORIGIN=DEMO,
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
        PYTHONPATH=str(ROOT),
        # The CSRF matrix makes ~45 counted requests per visitor; stay clear
        # of the 60/min default so a slow reset clone cannot turn it into 429.
        DEMO_REQUESTS_PER_MINUTE="600",
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
            "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
            "GOOGLE_APPLICATION_CREDENTIALS",
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


def test_demo_admin_dangerous_endpoints_are_forbidden(tmp_path):
    run_case("dangerous", tmp_path)


def test_demo_runs_make_no_external_calls(tmp_path):
    """§14/§28: every run kind in demo leaves egress/provider-call counts at 0."""
    run_case("runs", tmp_path)


def test_demo_rejects_injected_external_model(tmp_path):
    """§20: injecting an external model through scenario API/snapshot -> 422
    DEMO_UNSUPPORTED_CONFIGURATION in demo, with no catalog/probe/ledger rows.
    """
    run_case("injection", tmp_path)


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
    build_product_seed()
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
        assert response.status_code == 403
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

        # Pre-rotation token: valid before the role change; the role commit
        # bumps the CSRF revision and the next current-state GET syncs the
        # cookie (§10). The stale-token 403 loop below is the proof.
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
        assert response.status_code == 422
        assert code(response) == "DEMO_INVALID_REQUEST"
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
        # §9.1: a completed reset replays its stored 200 receipt.
        assert response.status_code == 200, response.text
        assert_no_cookie(response)
        response = await a.get("/demo/state")
        assert_demo_cookies(response)
        assert a.cookies.get("demo_csrftoken")

        # §9.1: reset X, then reset Y, then re-read X: the receipt is readable
        # and historical, but Y's mapping, cookies and role do not change.
        current = response.json()
        second = {
            "request_id": str(uuid4()),
            "expected_generation": current["generation"],
            "expected_auth_epoch": current["auth_epoch"],
            "confirm": True,
        }
        response = await a.post("/demo/reset", json=second, headers=headers(a))
        assert response.status_code in (200, 202), response.text
        await wait_ready(a, current["generation"] + 1)
        response = await a.get("/demo/state")  # syncs cookies to Y
        assert response.status_code == 200
        after_y = snapshot(response.json())
        assert after_y["generation"] == current["generation"] + 1
        jar = dict(a.cookies)
        response = await a.get(
            "/demo/state", params={"request_id": later_reset["request_id"]}
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["historical"] is True
        assert receipt["operation"] == "reset"
        assert receipt["request_id"] == later_reset["request_id"]
        assert receipt["result"]["next_url"] is None
        assert receipt["result"]["generation"] < after_y["generation"]
        assert snapshot(receipt["current"]) == after_y
        assert_no_cookie(response)
        assert dict(a.cookies) == jar
        assert snapshot((await a.get("/demo/state")).json()) == after_y


async def case_auth(tmp):
    import base64

    from itsdangerous import TimestampSigner, URLSafeTimedSerializer

    from src import main

    app = main.app
    build_product_seed()
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

        # A normal admin login cookie is ignored in demo (no normal session
        # middleware there): it neither authenticates nor elevates (§10).
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
        # §22 keeps the existing self-delete restriction (400).
        assert response.status_code == 400, response.text
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


NEW, CHILD, GEN = str(uuid4()), str(uuid4()), str(uuid4())
PASSWORD = {"expected_version": 1, "current_password": "x" * 12}
ACTION = {"request_id": NEW, "expected_version": 1}
CONV = f"/admin/comparison-conversations/{NEW}"
# Every POST of the §20 "forbidden" table (paths match src/api/routes/*).
DANGEROUS = (
    (
        "/admin/ai/providers/openai/key",
        {**PASSWORD, "api_key": "sk-demo-key-123456"},
    ),
    ("/admin/ai/providers/openai/enabled", {**PASSWORD, "enabled": True}),
    ("/admin/ai/providers/openai/delete", PASSWORD),
    ("/admin/ai/providers/openai/catalog", {"expected_version": 1}),
    (
        "/admin/ai/models",
        {
            "provider": "openai",
            "model_id": "gpt-5.5",
            "display_name": "Injected",
        },
    ),
    ("/admin/ai/models/1/update", {"expected_version": 1, "enabled": True}),
    ("/admin/ai/models/1/probes", {"request_id": NEW}),
    ("/admin/ai/settings/update", {"expected_version": 1}),
    ("/admin/comparisons", {"request_id": NEW, "message": "hi"}),
    (
        f"/admin/comparisons/{NEW}/panels/a/retry",
        {"request_id": NEW, "generation_id": GEN},
    ),
    ("/admin/comparison-conversations", {"request_id": NEW}),
    (f"{CONV}/turn", {**ACTION, "message": "hello"}),
    (
        f"{CONV}/adopt",
        {**ACTION, "child_id": CHILD, "panel_id": "a", "generation_id": GEN},
    ),
    (f"{CONV}/mentor", {**ACTION, "target_turn_id": GEN, "trigger": "manual"}),
    (f"{CONV}/end", ACTION),
    (
        f"{CONV}/children/{CHILD}/panels/a/retry",
        {**ACTION, "generation_id": GEN},
    ),
)
# Rows a forbidden route could leave behind: connections, models, probes,
# comparisons, the usage ledger and settings.
GUARDED_TABLES = (
    "provider_connection",
    "provider_audit_log",
    "model_config",
    "model_probe",
    "comparison_run",
    "comparison_conversation",
    "generation_run",
    "api_usage_log",
    "app_setting",
)


def guarded_counts(root):
    """Row counts of the single visitor DB, opened read-only."""
    (name,) = visitor_files(root)
    db = sqlite3.connect(f"file:{root / name}?mode=ro", uri=True)
    try:
        present = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert present >= set(GUARDED_TABLES), set(GUARDED_TABLES) - present
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in GUARDED_TABLES
        }
    finally:
        db.close()


async def case_dangerous(tmp):
    from src import main

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    build_product_seed()
    async with app.router.lifespan_context(app):
        admin = client_for(app)
        state = await start(admin, "admin")
        before = guarded_counts(root)
        failures = []
        for path, body in DANGEROUS:
            response = await admin.post(
                path, json=body, params=namespace(state), headers=headers(admin)
            )
            if (
                response.status_code != 403
                or code(response) != "DEMO_ACTION_FORBIDDEN"
            ):
                failures.append(
                    (path, response.status_code, response.text[:120])
                )
        print("DANGEROUS", failures)
        assert not failures, failures
        assert guarded_counts(root) == before


async def case_egress(tmp):
    from src import main

    app = main.app
    build_product_seed()
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


def build_product_seed():
    """The #197 CLI, as the production deploy builds it (outside the guard)."""
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.demo.seed",
            "build",
            "--out",
            os.environ["DEMO_SEED_DIR"],
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr[-500:]


def sse(response):
    """[(event, payload)] of one SSE body."""
    found = []
    for block in response.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line
        )
        if "event" in fields:
            found.append((fields["event"], json.loads(fields["data"])))
    return found


CONFIG_TABLES = (
    "provider_connection",
    "provider_audit_log",
    "model_config",
    "app_setting",
    "scenario",
)
# Rows a run (or an injected model) must never create in the visitor DB.
NO_ROWS = ("model_probe", "comparison_run", "comparison_conversation")


def visitor_db(root):
    (name,) = visitor_files(root)
    return sqlite3.connect(f"file:{root / name}?mode=ro", uri=True)


def table_rows(root, tables):
    db = visitor_db(root)
    try:
        return {
            t: db.execute(f'SELECT * FROM "{t}" ORDER BY 1').fetchall()
            for t in tables
        }
    finally:
        db.close()


def assert_local_ledger(root):
    """§13/§14/§19: every run and attempt row names the local provider only."""
    db = visitor_db(root)
    try:
        for table, column in (
            ("generation_run", "model"),
            ("api_usage_log", "model"),
        ):
            pairs = db.execute(
                f"SELECT DISTINCT provider, {column} FROM {table}"
            ).fetchall()
            assert pairs == [("demo", "demo-classroom-v1")], (table, pairs)
        for table in NO_ROWS:
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (
                0,
            )
        return {
            row[0]: row[1]
            for row in db.execute(
                "SELECT operation, COUNT(*) FROM generation_run "
                "GROUP BY operation"
            )
        }
    finally:
        db.close()


def add_failed_turn(root, session_id, content, *, turn_index):
    """A teacher turn whose student run failed: the state a retry starts from."""
    (name,) = visitor_files(root)
    turn_id, run_id = str(uuid4()), str(uuid4())
    with sqlite3.connect(root / name) as db:
        (owner,) = db.execute(
            "SELECT teacher_id FROM session WHERE id=?", (session_id,)
        ).fetchone()
        (config_hash,) = db.execute(
            "SELECT config_hash FROM session WHERE id=?", (session_id,)
        ).fetchone()
        db.execute(
            "INSERT INTO generation_run (id, owner_id, session_id, turn_id, "
            "operation, request_id, input_hash, config_hash, provider, model, "
            "status, error_code, started_at) VALUES (?,?,?,?,'student',?,?,?,"
            "'demo','demo-classroom-v1','failed','timeout_first_output',"
            "datetime('now'))",
            (
                run_id,
                owner,
                session_id,
                turn_id,
                str(uuid4()),
                "0" * 64,
                config_hash,
            ),
        )
        db.execute(
            "INSERT INTO message (session_id, role, content, turn_id, "
            "turn_index, created_at) VALUES (?, 'teacher', ?, ?, ?, "
            "datetime('now'))",
            (session_id, content, turn_id, turn_index),
        )
    return dict(turn_id=turn_id, run_id=run_id)


async def switch_role(client, role):
    state = (await client.get("/demo/state")).json()
    response = await client.post(
        "/demo/role",
        json={
            "request_id": str(uuid4()),
            "role": role,
            "expected_generation": state["generation"],
            "expected_auth_epoch": state["auth_epoch"],
        },
        headers=headers(client),
    )
    assert response.status_code == 200, response.text
    return (await client.get("/demo/state")).json()


async def case_runs(tmp):
    import asyncio

    from src import main
    from src.demo.seed import INPUTS

    root = Path(os.environ["DEMO_DATA_ROOT"])
    build_product_seed()
    async with main.app.router.lifespan_context(main.app):
        client = client_for(main.app)
        state = await start(client, "teacher")
        scenario = json.loads(
            (ROOT / "src/demo/fixtures/manifest.json").read_text()
        )["seed_refs"]["scenario"]
        seeded = Counter(assert_local_ledger(root))
        configured = table_rows(root, CONFIG_TABLES)

        async def post(path, body=None):
            return await client.post(
                path,
                json=body,
                params=namespace(state),
                headers=headers(client),
            )

        async def turn(sid, content, **extra):
            return await post(
                f"/sessions/{sid}/turns/stream",
                {"request_id": str(uuid4()), "content": content, **extra},
            )

        async def mentor(sid, turn_id, trigger):
            return await post(
                f"/sessions/{sid}/turns/{turn_id}/mentor/stream",
                {"request_id": str(uuid4()), "trigger": trigger},
            )

        def completed(response):
            events = sse(response)
            assert response.status_code == 200, response.text
            kinds = [kind for kind, _ in events]
            assert (
                kinds[0] == "run.accepted" and kinds[-1] == "output.completed"
            ), kinds
            assert events[-1][1]["provenance"]["external_ai_called"] is False
            return events[0][1]

        async def settled(path):
            for _ in range(900):
                run = (await client.get(path, params=namespace(state))).json()[
                    "latest_run"
                ]
                if run["status"] != "running":
                    assert run["status"] == "ok", (path, run)
                    assert (
                        run["outcome"]["provenance"]["external_ai_called"]
                        is False
                    )
                    return run
                await asyncio.sleep(0.2)
            raise AssertionError(("analysis still running", path))

        response = await post("/sessions", {"scenario_id": scenario})
        assert response.status_code == 201, response.text
        sid = response.json()["id"]

        # student: A -> common; a same-request replay is the stored run (§16).
        request_id = str(uuid4())
        body = {"request_id": request_id, "content": INPUTS["A"]}
        first = completed(await post(f"/sessions/{sid}/turns/stream", body))
        replay = await post(f"/sessions/{sid}/turns/stream", body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["run_id"] == first["run_id"]
        assert replay.json()["status"] == "completed"
        # mentor, manual then auto (a run per trigger).
        completed(await mentor(sid, first["turn_id"], "manual"))
        second = completed(await turn(sid, INPUTS["common"]))
        completed(await mentor(sid, second["turn_id"], "auto"))

        # legacy message POST: no run, no teacher row (old tabs reload).
        before = assert_local_ledger(root)
        legacy = await post(f"/sessions/{sid}/messages", {"content": "hi"})
        assert legacy.status_code == 410, legacy.text
        assert legacy.json()["code"] == "reload_required"
        assert assert_local_ledger(root) == before

        grown = Counter(assert_local_ledger(root)) - seeded
        assert grown == {"student": 2, "mentor": 2}, grown

        # analysis: teacher end (one run), a repeated analyze reuses it, then the
        # admin rerun is a new run (§16).
        response = await post(f"/sessions/{sid}/end")
        assert response.status_code == 202, response.text
        run = response.json()
        path = f"/sessions/{sid}/analysis/runs/{run['run_id']}"
        await settled(path)
        response = await post(
            f"/sessions/{sid}/analyze", {"request_id": str(uuid4())}
        )
        assert response.status_code == 200, response.text
        state = await switch_role(client, "admin")
        response = await post(
            f"/admin/sessions/{sid}/analyze_regenerate",
            {"request_id": str(uuid4())},
        )
        assert response.status_code == 202, response.text
        await settled(
            f"/admin/sessions/{sid}/analysis/runs/{response.json()['run_id']}"
        )

        # explicit retry (§16): a failed turn gets a new run on the same turn.
        # The public API cannot pick a failure fixture (§15), so the failed
        # first attempt is stored; the retry itself is a real run. It gets its
        # own session: §18 binds analysis refs to the two prepared turns only.
        state = await switch_role(client, "teacher")
        response = await post("/sessions", {"scenario_id": scenario})
        assert response.status_code == 201, response.text
        sid = response.json()["id"]
        said = "오늘은 여기까지 할게요."
        lost = add_failed_turn(root, sid, said, turn_index=1)
        retry = completed(await turn(sid, said, turn_id=lost["turn_id"]))
        assert retry["turn_id"] == lost["turn_id"]
        assert retry["run_id"] != lost["run_id"]
        again = await turn(sid, said, turn_id=retry["turn_id"])
        assert (
            again.status_code == 200 and again.json()["status"] == "completed"
        )
        assert again.json()["run_id"] == retry["run_id"]
        db = visitor_db(root)
        try:
            assert db.execute(
                "SELECT status FROM generation_run WHERE turn_id=? "
                "AND operation='student' ORDER BY rowid",
                (lost["turn_id"],),
            ).fetchall() == [("failed",), ("completed",)]
            assert db.execute(
                "SELECT COUNT(*) FROM message WHERE turn_id=? AND role='teacher'",
                (lost["turn_id"],),
            ).fetchone() == (1,)
        finally:
            db.close()

        grown = Counter(assert_local_ledger(root)) - seeded
        assert grown == {"student": 4, "mentor": 2, "analysis": 2}, grown
        assert table_rows(root, CONFIG_TABLES) == configured
        assert ATTEMPTS == [], ATTEMPTS


INJECTION_TABLES = (
    "provider_connection",
    "provider_audit_log",
    "model_config",
    "model_probe",
    "app_setting",
    "scenario",
    "scenario_group",
    "session",
    "message",
    "generation_run",
    "api_usage_log",
    "comparison_run",
    "comparison_conversation",
)


def external_scenario(action, **model):
    """The seed scenario with an external model on all three roles."""
    source = json.loads(
        (ROOT / "src/demo/fixtures/seed/scenario.json").read_text()
    )
    selection = dict(
        model_config_id=1,
        provider_connection_id=1,
        provider="openai",
        model_id="gpt-5.5",
        options=dict(max_output_tokens=4096),
    )
    selection.update(model)
    return dict(
        title=source["title"],
        subject=source["subject"],
        target_grade=source["target_grade"],
        groups=[1],
        config_schema_version=source["config_schema_version"],
        config={
            key: (
                dict(value, resolved_model_config=selection)
                if key in ("student", "mentor", "analysis")
                else value
            )
            for key, value in source["config"].items()
        },
        action=action,
    )


def inject_snapshot(root, session_id):
    """Stored data (not reachable by a client): an external model in a snapshot."""
    from src.services.lesson_snapshots import canonical_hash

    (name,) = visitor_files(root)
    with sqlite3.connect(root / name) as db:
        (raw,) = db.execute(
            "SELECT config_snapshot_json FROM session WHERE id=?", (session_id,)
        ).fetchone()
        envelope = json.loads(raw)
        for role in ("student", "mentor", "analysis"):
            envelope["config"][role]["resolved_model_config"].update(
                provider="openai", model_id="gpt-5.5", provider_connection_id=1
            )
        db.execute(
            "UPDATE session SET config_snapshot_json=?, config_hash=? "
            "WHERE id=?",
            (json.dumps(envelope), canonical_hash(envelope), session_id),
        )


async def case_injection(tmp):
    from src import main
    from src.demo.seed import INPUTS

    root = Path(os.environ["DEMO_DATA_ROOT"])
    build_product_seed()
    async with main.app.router.lifespan_context(main.app):
        client = client_for(main.app)
        state = await start(client, "admin")
        connections = dict(
            visitor_db(root).execute(
                "SELECT provider, id FROM provider_connection"
            )
        )
        demo = dict(
            model_config_id=1, provider_connection_id=connections["demo"]
        )
        before = table_rows(root, INJECTION_TABLES)

        async def post(path, body):
            return await client.post(
                path,
                json=body,
                params=namespace(state),
                headers=headers(client),
            )

        # §13/§20: every shape of "external model" is refused before any row.
        variants = {
            "unregistered model": dict(
                model_config_id=99, provider_connection_id=connections["openai"]
            ),
            "demo row relabelled": demo,
            "demo model on an external connection": dict(
                model_config_id=1,
                provider_connection_id=connections["openai"],
                model_id="demo-classroom-v1",
            ),
        }
        wrong = []
        for name, model in variants.items():
            for action in ("save_draft", "publish"):
                body = external_scenario(action, **model)
                for path, extra in (
                    ("/admin/scenarios", {}),
                    ("/admin/scenarios/1/update", {"expected_version": 1}),
                ):
                    response = await post(path, body | extra)
                    detail = response.json()["detail"]
                    got = (
                        detail.get("code")
                        if isinstance(detail, dict)
                        else detail
                    )
                    if (response.status_code, got) != (
                        422,
                        "DEMO_UNSUPPORTED_CONFIGURATION",
                    ):
                        wrong.append(
                            (name, action, path, response.status_code, got)
                        )
        assert table_rows(root, INJECTION_TABLES) == before

        # A run request cannot name a model or a fixture case: extras are inert.
        state = await switch_role(client, "teacher")
        response = await post("/sessions", {"scenario_id": 1})
        assert response.status_code == 201, response.text
        sid = response.json()["id"]
        response = await post(
            f"/sessions/{sid}/turns/stream",
            {
                "request_id": str(uuid4()),
                "content": INPUTS["A"],
                "provider": "openai",
                "model_id": "gpt-5.5",
                "model_config_id": 99,
                "resolved_model_config": {"provider": "openai"},
                "demo_selection": {"fixture_case": "timeout_total"},
                "fixture_case": "timeout_total",
            },
        )
        assert response.status_code == 200, response.text
        assert [k for k, _ in sse(response)][-1] == "output.completed"
        assert_local_ledger(root)

        # A stored snapshot that names an external model never reaches a call.
        response = await post("/sessions", {"scenario_id": 1})
        sid = response.json()["id"]
        inject_snapshot(root, sid)
        before = table_rows(root, INJECTION_TABLES)
        response = await post(
            f"/sessions/{sid}/turns/stream",
            {"request_id": str(uuid4()), "content": INPUTS["A"]},
        )
        assert response.status_code == 422, response.text
        assert code(response) == "DEMO_UNSUPPORTED_CONFIGURATION"
        assert table_rows(root, INJECTION_TABLES) == before
        assert_local_ledger(root)
        assert ATTEMPTS == [], ATTEMPTS
        # Last, so the "no rows" checks above run even when the code differs.
        assert not wrong, wrong


if __name__ == "__main__":
    import asyncio

    case, tmp = sys.argv[1], Path(sys.argv[2])
    install_egress_guard()

    async def run():
        try:
            await prove_egress_guard()
            # The guard is proven before the app (or a provider) is imported.
            loaded = [
                m for m in sys.modules if m == "src" or m.startswith("src.")
            ]
            assert not loaded, loaded
            import src.main  # noqa: F401

            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())
    # Whatever the case did, nothing reached a socket or an SDK client.
    assert ATTEMPTS == [], ATTEMPTS

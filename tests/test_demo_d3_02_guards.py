"""D3-02 (#200): risky admin endpoints are demo-forbidden or read-only (§20)."""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from src.demo.database import DemoError

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ("s" * 48, "c" * 48, "n" * 48)
API_KEY = "sk-demo-must-not-be-read"
TABLES = (
    "provider_connection",
    "provider_audit_log",
    "model_config",
    "model_probe",
    "comparison_run",
    "generation_run",
    "api_usage_log",
    "app_setting",
)
PROVENANCE = {
    "mode": "demo",
    "response_source": "prepared_example",
    "external_ai_called": False,
    "contract_version": "d0-03-v1-r3",
    "content_version": "d0-02-v1",
    "fixture_version": "demo-fixture-v1",
    "record_origin": "seed_prepared",
}


def run_case(case, tmp_path):
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
        PYTHONPATH=str(ROOT),
    )
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not any(secret in output for secret in (*SECRETS, API_KEY)), output
    assert str(tmp_path) not in output, output
    return result


@pytest.mark.parametrize("case", ["forbidden", "teacher", "reads", "stops"])
def test_demo_risky_endpoint_guards(case, tmp_path):
    run_case(case, tmp_path)


async def test_demo_reauthenticate_fails_closed_before_limiter_and_password(
    monkeypatch,
):
    from src.services import provider_reauthentication as module

    def boom(*args, **kwargs):
        raise AssertionError("limiter or password reached in demo")

    class User:
        id = 1
        verify_password = boom

    monkeypatch.setattr(module, "config", SimpleNamespace(APP_MODE="demo"))
    monkeypatch.setattr(
        module, "reauth_limiter", SimpleNamespace(test=boom, hit=boom)
    )
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"))
    with pytest.raises(DemoError) as caught:
        await module.reauthenticate(request, User(), SecretStr("x" * 12))
    assert caught.value.code == "DEMO_ACTION_FORBIDDEN"
    assert caught.value.status_code == 403


async def test_demo_read_only_guard_passes_the_admin_in_normal_mode():
    from src.api.routes import admin_providers
    from src.config import config

    assert config.APP_MODE == "normal"
    user = object()
    assert await admin_providers.demo_read_only(user) is user


# --------------------------------------------------------------------------
# Subprocess cases (APP_MODE=demo, TESTING=false, isolated environment).

NEW = str(uuid4())
CHILD = str(uuid4())
GEN = str(uuid4())
STOP_ALL = {"generations": [{"panel_id": "a", "generation_id": GEN}]}
STOP_PANEL = {"generation_id": GEN}
ACTION = {"request_id": NEW, "expected_version": 1}
KEY_BODY = {
    "expected_version": 1,
    "current_password": "wrong",
    "api_key": API_KEY,
}
PASSWORD_BODY = {"expected_version": 1, "current_password": "wrong"}
ENABLED_BODY = {**PASSWORD_BODY, "enabled": True}
CATALOG_BODY = {"expected_version": 1}
CONV = f"/admin/comparison-conversations/{NEW}"
CMP = f"/admin/comparisons/{NEW}"

# (path, plausible body) for every POST that demo refuses.
FORBIDDEN = [
    *(
        (f"/admin/ai/providers/{provider}/{action}", body)
        for provider in ("openai", "anthropic", "google")
        for action, body in (
            ("key", KEY_BODY),
            ("enabled", ENABLED_BODY),
            ("delete", PASSWORD_BODY),
            ("catalog", CATALOG_BODY),
        )
    ),
    ("/admin/ai/models", {"provider": "openai", "model_id": "gpt-x"}),
    ("/admin/ai/models/1/update", {"expected_version": 1, "enabled": True}),
    ("/admin/ai/models/1/probes", {"request_id": NEW}),
    ("/admin/ai/settings/update", {"expected_version": 1}),
    ("/admin/comparisons", {"request_id": NEW, "message": "hi"}),
    (
        f"{CMP}/panels/a/retry",
        {"request_id": NEW, "generation_id": GEN},
    ),
    ("/admin/comparison-conversations", {"request_id": NEW}),
    (f"{CONV}/turn", {**ACTION, "message": "hello"}),
    (
        f"{CONV}/adopt",
        {**ACTION, "child_id": CHILD, "panel_id": "a", "generation_id": GEN},
    ),
    (
        f"{CONV}/mentor",
        {**ACTION, "target_turn_id": GEN, "trigger": "manual"},
    ),
    (f"{CONV}/end", ACTION),
    (
        f"{CONV}/children/{CHILD}/panels/a/retry",
        {**ACTION, "generation_id": GEN},
    ),
]


def counts(root):
    """Row counts of the single visitor DB, opened read-only."""
    from test_demo_d1_04_app import visitor_files

    (name,) = visitor_files(root)
    db = sqlite3.connect(f"file:{root / name}?mode=ro", uri=True)
    try:
        present = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert present >= set(TABLES), set(TABLES) - present
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in TABLES
        }
    finally:
        db.close()


class Spies:
    """Records sockets and the reauth limiter/password check."""

    def __init__(self):
        import socket

        from src.models.user import User
        from src.services import provider_reauthentication as module

        self.sockets, self.reauth = [], []
        real_connect = socket.socket.connect

        def connect(sock, address):
            if sock.family != socket.AF_UNIX:
                self.sockets.append(address)
            return real_connect(sock, address)

        def create_connection(address, *args, **kwargs):
            self.sockets.append(address)
            raise OSError("blocked")

        socket.socket.connect = connect
        socket.create_connection = create_connection

        def spy(name):
            def call(*args, **kwargs):
                self.reauth.append(name)
                raise AssertionError("reauth ran in demo")

            return call

        module.reauth_limiter = SimpleNamespace(
            test=spy("test"), hit=spy("hit")
        )
        User.verify_password = spy("verify_password")

    def clean(self):
        assert self.sockets == [], self.sockets
        assert self.reauth == [], self.reauth


async def boot(role="admin"):
    from test_demo_d1_04_app import (
        client_for,
        install_seed,
        make_seed,
        start,
    )

    from src import main

    install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    return main.app, client_for, start


async def call(client, state, method, path, body=None):
    from test_demo_d1_04_app import headers, namespace

    extra = {} if method == "GET" else {"headers": headers(client)}
    if body is not None:
        extra["json"] = body
    return await client.request(method, path, params=namespace(state), **extra)


def check_error(response, status, expected):
    from test_demo_d1_04_app import code

    assert response.status_code == status, response.text
    assert code(response) == expected, response.text
    assert "set-cookie" not in response.headers


async def case_forbidden(tmp):
    app, client_for, start = await boot()
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")
        before = counts(root)
        for path, body in FORBIDDEN:
            # {} proves the guard runs before body validation.
            for sent in ({}, body):
                response = await call(client, state, "POST", path, sent)
                check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
                assert API_KEY not in response.text
        assert counts(root) == before
        spies.clean()


async def case_teacher(tmp):
    app, client_for, start = await boot()
    spies = Spies()
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")
        from test_demo_d1_04_app import headers

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
        assert state["role"] == "teacher"
        for path, body in (
            FORBIDDEN[0],
            ("/admin/ai/models", {}),
            ("/admin/comparisons", {}),
        ):
            response = await call(client, state, "POST", path, body)
            check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        for path in ("/admin/ai/state", "/admin/comparisons"):
            response = await call(client, state, "GET", path)
            check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        spies.clean()


async def case_reads(tmp):
    app, client_for, start = await boot()
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")
        before = counts(root)

        response = await call(client, state, "GET", "/admin/ai/state")
        assert response.status_code == 200, response.text
        assert response.json()["provenance"] == PROVENANCE
        assert "set-cookie" not in response.headers

        for path in ("/admin/comparisons", "/admin/comparison-conversations"):
            response = await call(client, state, "GET", path)
            assert response.status_code == 200, (path, response.text)
            assert "set-cookie" not in response.headers
            body = response.json()
            items = body if isinstance(body, list) else body.get("items")
            assert items == [], (path, body)

        for path in (
            f"/admin/ai/probes/{NEW}",
            f"{CMP}",
            f"{CMP}/panels/a/model",
            f"{CONV}",
            f"{CONV}/panels/a/model",
        ):
            response = await call(client, state, "GET", path)
            check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")

        response = await call(client, state, "GET", "/admin/model-comparison")
        assert response.status_code == 200, response.text[:500]
        assert counts(root) == before
        spies.clean()


async def case_stops(tmp):
    app, client_for, start = await boot()
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")
        before = counts(root)
        for method, path, body in (
            ("POST", f"/admin/ai/probes/{NEW}/cancel", {}),
            ("POST", f"{CMP}/stop", STOP_ALL),
            ("POST", f"{CMP}/panels/a/stop", STOP_PANEL),
            (
                "POST",
                f"{CONV}/stop",
                {**ACTION, "child_id": CHILD, **STOP_ALL},
            ),
            ("DELETE", CMP, None),
            ("DELETE", CONV, None),
        ):
            response = await call(client, state, method, path, body)
            check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")
        assert counts(root) == before
        spies.clean()


if __name__ == "__main__":
    from test_demo_d1_04_app import CLIENTS

    case, tmp = sys.argv[1], Path(sys.argv[2])

    async def run():
        try:
            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())

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


def run_case(case, tmp_path, **settings):
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
        **settings,
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


@pytest.mark.parametrize(
    "case", ["forbidden", "teacher", "reads", "stops", "existing", "analysis"]
)
def test_demo_risky_endpoint_guards(case, tmp_path):
    run_case(case, tmp_path)


@pytest.mark.parametrize(
    "case, settings",
    [("quota", {}), ("scenario_full", {"DEMO_MAX_SCENARIOS": "4"})],
)
def test_demo_allowed_crud_quotas(case, settings, tmp_path):
    run_case(case, tmp_path, **settings)


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


async def boot(seed=None):
    from test_demo_d1_04_app import (
        client_for,
        install_seed,
        make_seed,
        start,
    )

    from src import main

    root = Path(os.environ["DEMO_DATA_ROOT"])
    install_seed(main, await (seed or make_seed)(root))
    return main.app, client_for, start


STRIPPED = (
    "session",
    "message",
    "generation_run",
    "session_summary",
    "session_feedback_report",
)
AT, LATER = "2026-10-18 00:00:00", "2099-01-01 00:00:00"
RUN, CONV_RUN, PROBE = str(uuid4()), str(uuid4()), str(uuid4())
CMP_ID, CONV_ID, CHILD_ID = str(uuid4()), str(uuid4()), str(uuid4())
HASH = "0" * 64


def comparison_row(identifier, status, conversation=None):
    return (
        "INSERT INTO comparison_run(id,admin_id,source_snapshot,source_hash,"
        "target_role,mode,canonical_context,context_hash,panel_configs,"
        "request_id,input_hash,created_at,expires_at,status,conversation_id)"
        " VALUES(?,1,'{}',?,'student','same_context','[]',?,"
        """'[{"panel_id":"a"}]',?,?,?,?,?,?)""",
        (identifier, HASH, HASH, str(uuid4()), HASH, AT, LATER, status)
        + (conversation,),
    )


def run_row(identifier, comparison, status):
    return (
        "INSERT INTO generation_run(id,owner_id,turn_id,operation,request_id,"
        "input_hash,config_hash,provider,model,status,started_at,"
        "comparison_run_id,panel_id) VALUES(?,1,?,'student',?,?,?,'demo',"
        "'demo-classroom-v1',?,?,?,'a')",
        (identifier, str(uuid4()), str(uuid4()), HASH, HASH, status, AT)
        + (comparison,),
    )


async def journey_seed(root):
    """make_seed strips the session tables; restore the migrated schema and
    add one existing row per guarded resource, copied into every visitor."""
    from contextlib import closing

    from sqlalchemy.ext.asyncio import create_async_engine
    from test_demo_d1_04_app import make_seed

    from src.db.migrations.migrate import run_all_migrations

    spec = await make_seed(root)
    fresh = root.parent / "schema.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{fresh}")
    await run_all_migrations(db_engine=engine)
    await engine.dispose()
    with closing(sqlite3.connect(fresh)) as db:
        ddl = [
            row[0]
            for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND "
                f"tbl_name IN ({','.join('?' * len(STRIPPED))}) "
                "ORDER BY type != 'table'",
                STRIPPED,
            )
        ]
    with closing(sqlite3.connect(spec.path)) as db, db:
        for table in STRIPPED:
            db.execute(f'DROP TABLE "{table}"')
        for sql in ddl:
            db.execute(sql)
        for statement in (
            (
                "INSERT INTO scenario(id,title,is_active,created_by,created_at,"
                "status,config_json) VALUES(1,'Fictional',1,1,?,'published','{}')",
                (AT,),
            ),
            (
                "INSERT INTO session(id,scenario_id,teacher_id,started_at,"
                "tutor_intervention_count,tutor_question_count,"
                "config_snapshot_json,snapshot_origin) "
                "VALUES(1,1,2,?,0,0,'{}','native')",
                (AT,),
            ),
            (
                "INSERT INTO provider_connection(id,provider,enabled) "
                "VALUES(4,'demo',1)",
                (),
            ),
            (
                "INSERT INTO model_config(id,provider_connection_id,model_id,"
                "display_name) VALUES(1,4,'demo-classroom-v1','Demo')",
                (),
            ),
            (
                "INSERT INTO model_probe(id,owner_id,model_config_id,role,"
                "request_id,fingerprint,credential_revision,connection_version,"
                "config_version,capability_definition_version,"
                "role_contract_version,options_json,status,started_at) "
                "VALUES(1,1,1,'student',?,'x',0,1,1,'demo-cap-v1','s1-v1',"
                "'{}','verifying',?)",
                (PROBE, AT),
            ),
            comparison_row(CMP_ID, "running"),
            run_row(RUN, CMP_ID, "running"),
            (
                "INSERT INTO comparison_conversation(id,admin_id,source_version,"
                "source_snapshot,source_hash,panel_configs,state_json,version,"
                "request_id,input_hash,created_at,expires_at) "
                "VALUES(?,1,1,'{}',?,'[]','{}',1,?,?,?,?)",
                (CONV_ID, HASH, str(uuid4()), HASH, AT, LATER),
            ),
            comparison_row(CHILD_ID, "finished", CONV_ID),
            run_row(CONV_RUN, CHILD_ID, "completed"),
        ):
            db.execute(*statement)
    return spec


def install_register(app, cancelled):
    """GET /probe-register adds a real token to the visitor's own scope in
    the app's bound lifecycle; cancelling it records (scope, kind, id)."""
    from fastapi import Depends, Request
    from fastapi.routing import request_response

    from src.api import dependencies as deps
    from src.demo.lifecycle import TaskControl

    tokens = {}

    @app.get("/probe-register")
    async def probe_register(
        request: Request, user=Depends(deps.get_current_user)
    ):
        ctx = request.state.demo_context
        kind = request.query_params["kind"]
        identifier = request.query_params["id"]
        identifier = int(identifier) if kind == "probe" else identifier
        done = asyncio.Event()

        def cancel():
            cancelled.append((ctx.scope, kind, identifier))
            done.set()

        token = await app.state.demo_request_db.lifecycle.register(
            ctx, kind, identifier, TaskControl(cancel, done)
        )
        tokens[ctx.scope, kind, identifier] = token
        return {"scope": list(ctx.scope)}

    route = app.routes[-1]
    route.app = request_response(
        deps.demo_request_handler(route.get_route_handler())
    )
    return tokens


async def register(client, state, kind, identifier):
    from test_demo_d1_04_app import namespace

    response = await client.get(
        "/probe-register",
        params={**namespace(state), "kind": kind, "id": identifier},
    )
    assert response.status_code == 200, response.text
    return tuple(response.json()["scope"])


def visitor_rows(root, sql, params=()):
    """One result per visitor DB, opened read-only."""
    from test_demo_d1_04_app import visitor_files

    rows = []
    for name in visitor_files(root):
        db = sqlite3.connect(f"file:{root / name}?mode=ro", uri=True)
        try:
            rows.append(db.execute(sql, params).fetchone())
        finally:
            db.close()
    return sorted(rows, key=repr)


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
    from test_demo_d1_04_app import headers, namespace

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
        # §20 precedence: refused before the comparison body reader.
        big = '{"pad":"' + "x" * (3 * 1024 * 1024) + '"}'
        for path in ("/admin/comparisons", f"{CONV}/turn"):
            for content in (big.encode(), b'{"message":"\xff"}'):
                response = await client.post(
                    path,
                    content=content,
                    params=namespace(state),
                    headers={
                        **headers(client),
                        "content-type": "application/json",
                    },
                )
                check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        # Permitted stops keep the normal size limit.
        response = await client.post(
            f"{CMP}/stop",
            content=big.encode(),
            params=namespace(state),
            headers={**headers(client), "content-type": "application/json"},
        )
        assert response.status_code == 413, response.text
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


async def case_existing(tmp):
    """§20: stops, cancel and deletes reach only the caller's real tokens,
    though both visitors hold identical ids, without touching the singleton."""
    from src.demo.lifecycle import NORMAL_SCOPE
    from src.demo.lifecycle import lifecycle as singleton

    app, client_for, start = await boot(journey_seed)
    cancelled = []
    tokens = install_register(app, cancelled)
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        registry = app.state.demo_request_db.lifecycle
        assert registry is not singleton
        a, b = client_for(app), client_for(app, "10.0.0.2")
        state_a, state_b = await start(a, "admin"), await start(b, "admin")
        scopes = []
        for client, state in ((a, state_a), (b, state_b)):
            for kind, identifier in (
                ("probe", 1),
                ("run", RUN),
                ("run", CONV_RUN),
            ):
                scope = await register(client, state, kind, identifier)
            scopes.append(scope)
        scope_a, scope_b = scopes
        assert scope_a != scope_b

        response = await call(
            a, state_a, "POST", f"/admin/ai/probes/{PROBE}/cancel", {}
        )
        assert response.status_code == 200, response.text
        assert cancelled == [(scope_a, "probe", 1)]
        statuses = visitor_rows(root, "SELECT status FROM model_probe")
        assert len(statuses) == 2 and statuses.count(("verifying",)) == 1

        path = f"/admin/comparisons/{CMP_ID}"
        response = await call(
            a, state_a, "POST", f"{path}/panels/a/stop", {"generation_id": RUN}
        )
        assert response.status_code == 200, response.text
        assert cancelled[1:] == [(scope_a, "run", RUN)]
        response = await call(
            b,
            state_b,
            "POST",
            f"{path}/stop",
            {"generations": [{"panel_id": "a", "generation_id": RUN}]},
        )
        assert response.status_code == 200, response.text
        assert cancelled[2:] == [(scope_b, "run", RUN)]

        # Terminal rows whose tokens still clean up are busy only in their scope.
        conversation = f"/admin/comparison-conversations/{CONV_ID}"
        for target, identifier in ((path, RUN), (conversation, CONV_RUN)):
            response = await call(a, state_a, "DELETE", target)
            assert response.status_code == 409, response.text
            token = tokens[scope_a, "run", identifier]
            token.task_control.done.set()
            await registry.pop(*token.key, token)
            response = await call(a, state_a, "DELETE", target)
            assert response.status_code == 204, response.text
            response = await call(b, state_b, "DELETE", target)
            assert response.status_code == 409, response.text
        assert visitor_rows(root, "SELECT COUNT(*) FROM comparison_run") == [
            (0,),
            (2,),
        ]
        assert len(cancelled) == 3
        assert singleton.tokens(NORMAL_SCOPE) == ()
        spies.clean()


async def case_analysis(tmp):
    """§22/§24.1: admin end, regenerate and cancel run under the visitor's
    write guard and the app's bound lifecycle."""
    from src.demo.lifecycle import lifecycle as singleton
    from src.models import GenerationRun
    from src.services import analysis_runs

    app, client_for, start = await boot(journey_seed)
    cancelled, reserved = [], []
    install_register(app, cancelled)
    runs = [str(uuid4()), str(uuid4())]

    async def reserve(factory, session_id, actor_id, request_id, **kwargs):
        # Stands in for plan/model work with one real guarded visitor write.
        ctx, registry = kwargs["ctx"], kwargs["registry"]
        reserved.append((ctx.scope, registry, kwargs["regenerate"]))
        run_id = runs[len(reserved) - 1]
        async with registry.write_guard(ctx), factory() as db:
            from src.models import Session

            assert (await db.get(Session, session_id)).ended_at is not None
            db.add(
                GenerationRun(
                    id=run_id,
                    owner_id=actor_id,
                    session_id=session_id,
                    turn_id=str(uuid4()),
                    operation="analysis",
                    request_id=request_id,
                    input_hash=HASH,
                    config_hash=HASH,
                    provider="demo",
                    model="demo-classroom-v1",
                    status="running",
                )
            )
            await db.commit()
        return {"latest_run": {"run_id": run_id, "status": "running"}}, None

    async def rendered(db, run, *, admin=False):
        # The report view needs a real lesson snapshot; the cancel is already done.
        return {"run_id": run.id, "status": run.status}

    analysis_runs.reserve_analysis = reserve
    analysis_runs.run_response = rendered
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        registry = app.state.demo_request_db.lifecycle
        a, b = client_for(app), client_for(app, "10.0.0.2")
        state_a, state_b = await start(a, "admin"), await start(b, "admin")
        scope_b = await register(b, state_b, "analysis", runs[0])

        body = {"request_id": str(uuid4())}
        response = await call(a, state_a, "POST", "/admin/sessions/1/end", body)
        assert response.status_code == 202, response.text
        scope_a = await register(a, state_a, "analysis", runs[0])
        assert reserved == [(scope_a, registry, False)]
        assert visitor_rows(
            root, "SELECT ended_at IS NOT NULL FROM session WHERE id=1"
        ) == [(0,), (1,)]

        response = await call(
            a,
            state_a,
            "POST",
            f"/admin/sessions/1/analysis/runs/{runs[0]}/cancel",
            {},
        )
        assert response.status_code == 202, response.text
        assert cancelled == [(scope_a, "analysis", runs[0])]
        assert scope_b != scope_a
        assert visitor_rows(
            root, "SELECT status FROM generation_run WHERE id=?", (runs[0],)
        ) == [("cancelled",), None]

        body = {"request_id": str(uuid4())}
        response = await call(
            a, state_a, "POST", "/admin/sessions/1/analyze_regenerate", body
        )
        assert response.status_code == 202, response.text
        assert reserved[1:] == [(scope_a, registry, True)]
        assert registry is not singleton
        spies.clean()


async def quota_seed(root):
    """Four live scenarios and one soft-deleted: the deleted row frees its slot."""
    from contextlib import closing

    from test_demo_d1_04_app import make_seed

    spec = await make_seed(root)
    with closing(sqlite3.connect(spec.path)) as db, db:
        for identifier in range(1, 6):
            db.execute(
                "INSERT INTO scenario(id,title,is_active,created_by,created_at,"
                "status,config_json,deleted_at) "
                "VALUES(?,'Fictional',1,1,?,'draft','{}',?)",
                (identifier, AT, AT if identifier == 5 else None),
            )
    return spec


def draft():
    from src.api.schemas.scenario_config import ScenarioConfig

    return {
        "title": "Fictional draft",
        "config_schema_version": 1,
        "config": ScenarioConfig(
            problem={}, student={}, mentor={}, analysis={}, runtime={}
        ).model_dump(mode="json"),
        "action": "save_draft",
    }


def bulk_csv(count, start=0):
    lines = ["username,nickname,role,group"]
    lines += [f"visitor_{start + i},Visitor {i},teacher," for i in range(count)]
    return ("\n".join(lines) + "\n").encode()


async def upload(client, state, content):
    from test_demo_d1_04_app import headers, namespace

    return await client.post(
        "/admin/users/bulk/preview",
        params=namespace(state),
        headers=headers(client),
        files={"file": ("users.csv", content, "text/csv")},
    )


async def case_quota(tmp):
    """§8/§21: allowed CRUD hits per-visitor hard quotas from config only."""
    from src.config import config

    app, client_for, start = await boot(quota_seed)
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")

        # Groups: seed has none; two racers for the last slot get one 201.
        limit = config.DEMO_MAX_GROUPS
        for index in range(limit - 1):
            response = await call(
                client, state, "POST", "/admin/groups", {"name": f"g{index}"}
            )
            assert response.status_code == 201, response.text
        racers = await asyncio.gather(
            *(
                call(client, state, "POST", "/admin/groups", {"name": name})
                for name in ("last-a", "last-b")
            )
        )
        assert sorted(r.status_code for r in racers) == [201, 429], [
            r.text for r in racers
        ]
        loser = next(r for r in racers if r.status_code == 429)
        check_error(loser, 429, "DEMO_LIMIT_REACHED")
        assert visitor_rows(root, "SELECT COUNT(*) FROM user_group") == [
            (limit,)
        ]
        group_id = next(r for r in racers if r.status_code == 201).json()["id"]
        response = await call(
            client, state, "POST", f"/admin/groups/{group_id}/delete", None
        )
        assert response.status_code == 200, response.text
        response = await call(
            client, state, "POST", "/admin/groups", {"name": "again"}
        )
        assert response.status_code == 201, response.text

        # Contributors.
        body = {"name": "Fictional", "affiliation": "Demo", "bio": "Example"}
        for _ in range(config.DEMO_MAX_CONTRIBUTORS):
            response = await call(client, state, "POST", "/admin/about", body)
            assert response.status_code == 201, response.text
        response = await call(client, state, "POST", "/admin/about", body)
        check_error(response, 429, "DEMO_LIMIT_REACHED")

        # Scenarios: 4 live of 5 passes the quota to the existing validators.
        response = await call(
            client, state, "POST", "/admin/scenarios", draft()
        )
        assert response.status_code == 422, response.text
        assert "common_model_required" in response.text

        # Bulk: bytes 413, rows above min(upload rows, users left) 429.
        users_left = config.DEMO_MAX_USERS - 2
        rows = min(config.DEMO_MAX_UPLOAD_ROWS, users_left)
        oversized = bulk_csv(1) + b"#" * config.DEMO_MAX_UPLOAD_BYTES
        check_error(
            await upload(client, state, oversized), 413, "DEMO_UPLOAD_TOO_LARGE"
        )
        check_error(
            await upload(client, state, bulk_csv(rows + 1)),
            429,
            "DEMO_LIMIT_REACHED",
        )
        response = await upload(client, state, bulk_csv(rows))
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["valid"] == rows
        entries = [
            {"username": f"visitor_{i}", "nickname": f"Visitor {i}"}
            for i in range(rows + 1)
        ]
        response = await call(
            client,
            state,
            "POST",
            "/admin/users/bulk/register",
            {"users": entries},
        )
        check_error(response, 429, "DEMO_LIMIT_REACHED")
        response = await call(
            client,
            state,
            "POST",
            "/admin/users/bulk/register",
            {"users": entries[:rows]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["success_count"] == rows
        assert visitor_rows(root, "SELECT COUNT(*) FROM user") == [(2 + rows,)]
        if 2 + rows == config.DEMO_MAX_USERS:
            check_error(
                await upload(client, state, bulk_csv(1, start=100)),
                429,
                "DEMO_LIMIT_REACHED",
            )
        spies.clean()


async def case_scenario_full(tmp):
    """With DEMO_MAX_SCENARIOS=4 the four live rows fill the quota."""
    app, client_for, start = await boot(quota_seed)
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "admin")
        response = await call(
            client, state, "POST", "/admin/scenarios", draft()
        )
        check_error(response, 429, "DEMO_LIMIT_REACHED")
        assert visitor_rows(root, "SELECT COUNT(*) FROM scenario") == [(5,)]


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

"""D4-01 (#203): two visitors from one seed never see each other's data.

Registry level runs in-process; the HTTP level runs the real demo app in a
subprocess (APP_MODE=demo, TESTING=false) with the real cookie/CSRF middleware.
"""

import asyncio
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_demo_d1_01_visitors import ready_context
from test_demo_d1_01_visitors import visitors as shared_registry  # noqa: F401
from test_demo_d1_04_app import (
    CLIENTS,
    SECRETS,
    client_for,
    code,
    headers,
    namespace,
    start,
    visitor_files,
    visitor_of,
    wait_ready,
)

from src.demo.database import DemoError

SUFFIX = [
    "demo_response_source",
    "demo_external_ai_called",
    "demo_content_version",
    "demo_fixture_version",
    "demo_record_origin",
]
QUERIES = {
    "users": 'SELECT id, nickname FROM "user" ORDER BY id',
    "groups": "SELECT id, name FROM user_group ORDER BY id",
    "records": "SELECT id, body FROM synthetic_record ORDER BY id",
    "sessions": "SELECT id, teacher_id, deleted_at FROM session ORDER BY id",
    "messages": "SELECT session_id, role, content FROM message ORDER BY id",
}


# The product seed has no synthetic_record table; its third table is the run
# ledger (a visitor's own reset must bring it back to the seed too).
PRODUCT_QUERIES = {
    **{k: v for k, v in QUERIES.items() if k != "records"},
    "runs": "SELECT id, operation, status FROM generation_run ORDER BY id",
}


def file_rows(path, queries=QUERIES):
    """Committed rows of one visitor file, read outside the app's engine."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            name: db.execute(sql).fetchall() for name, sql in queries.items()
        }
    finally:
        db.close()


def product_rows(path):
    return file_rows(path, PRODUCT_QUERIES)


LEDGER_TABLES = (
    "session",
    "message",
    "generation_run",
    "api_usage_log",
    "question_analysis",
    "session_summary",
    "session_feedback_report",
)


def ledger_rows(path):
    """Every run/ledger/analysis table of one visitor file, whole rows."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in LEDGER_TABLES
        }
    finally:
        db.close()


def contents(path, session_id):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        sql = "SELECT content FROM message WHERE session_id=?"
        return [c for (c,) in db.execute(sql, (session_id,))]
    finally:
        db.close()


# --------------------------------------------------------------------------
# Registry level (§3, §5/§5.1, §6, §7): in-process, src.demo.* only.


@pytest.fixture
async def pair(shared_registry):  # noqa: F811
    _, a, ctx_a = await ready_context(shared_registry)
    _, b, ctx_b = await ready_context(shared_registry)
    yield SimpleNamespace(r=shared_registry, a=a, b=b, ca=ctx_a, cb=ctx_b)


async def rows(ctx):
    async with ctx.factory() as db:
        users = await db.execute(text('SELECT id, nickname FROM "user"'))
        record = await db.execute(text("SELECT id, body FROM synthetic_record"))
        return sorted(map(tuple, users)), sorted(map(tuple, record))


async def observe(reg, visitor):
    s = await reg.snapshot(visitor.verified_visitor)
    return (s.state, s.generation, s.auth_epoch, s.browser_scope_id, s.role)


async def test_same_seed_gives_separate_files_and_writes_stay_private(pair):
    r, a, b = pair.r, pair.a.snapshot.ref, pair.b.snapshot.ref
    assert a.path != b.path and a.factory is not b.factory
    seed_users = [(1, "Fictional admin"), (2, "Fictional teacher")]
    assert (
        file_rows(a.path)["users"] == file_rows(b.path)["users"] == seed_users
    )
    before = await rows(pair.cb)
    async with pair.ca.factory() as db:
        async with r.write_guard(pair.ca):
            await db.execute(
                text("UPDATE \"user\" SET nickname='A' WHERE id=2")
            )
            await db.execute(
                text("INSERT INTO synthetic_record VALUES (2,'A')")
            )
            await db.commit()
    assert (await rows(pair.ca))[0][1] == (2, "A")
    assert file_rows(a.path)["records"][-1] == (2, "A")
    # B sees neither through its factory nor in its own file.
    assert await rows(pair.cb) == before
    assert file_rows(b.path)["users"] == seed_users
    assert file_rows(b.path)["records"] == [(1, "fictional")]


async def test_reset_and_expiry_of_one_visitor_leave_the_other_untouched(pair):
    r, ref_b = pair.r, pair.b.snapshot.ref
    before = await observe(r, pair.b)
    rows_before = await rows(pair.cb)
    reset = await r.reset_visit(pair.ca, str(uuid4()))
    await r.release_lease(pair.ca.lease)
    candidate = await r.prepare(reset.operation)
    async with r.transition(pair.a.verified_visitor) as handle:
        assert handle.publish(reset.operation, candidate).generation == 2
    assert await observe(r, pair.b) == before
    assert r.lease_count(pair.cb.scope) == 1 and ref_b.path.exists()
    assert await rows(pair.cb) == rows_before

    await r.expire_visit(pair.a.verified_visitor, "ttl")
    assert (await observe(r, pair.a))[0] in ("expired", "closing")
    assert await observe(r, pair.b) == before
    assert r.lease_count(pair.cb.scope) == 1 and ref_b.path.exists()
    assert await rows(pair.cb) == rows_before
    assert (await r.check(pair.cb)).ref is ref_b


async def test_a_context_is_refused_against_the_other_visitor_without_writes(
    pair,
):
    r = pair.r
    before = (await rows(pair.ca), await rows(pair.cb))
    async with r.transition(pair.b.verified_visitor) as handle:
        with pytest.raises(DemoError) as raised:
            handle.check(pair.ca)
    assert raised.value.code == "DEMO_INVALID_REQUEST"
    # Same generation/epoch/role, only the visitor binding is forged.
    forged = replace(pair.ca, verified_visitor=pair.b.verified_visitor)
    with pytest.raises(DemoError) as raised:
        async with r.write_guard(forged):
            pytest.fail("a foreign context was allowed to write")
    # The lease belongs to A's generation ref, so B's registry sees it stale.
    assert raised.value.code == "DEMO_STALE_GENERATION"
    # A's raw factory cannot write under B's guard.
    async with pair.ca.factory() as db:
        async with r.write_guard(pair.cb):
            with pytest.raises(DemoError) as raised:
                await db.execute(text("UPDATE synthetic_record SET body='x'"))
        await db.rollback()
    assert raised.value.code == "DEMO_INVALID_REQUEST", raised.value.code
    assert (await rows(pair.ca), await rows(pair.cb)) == before
    assert r.lease_count(pair.ca.scope) == r.lease_count(pair.cb.scope) == 1


# --------------------------------------------------------------------------
# HTTP level: real app, real middleware (subprocess).


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
        DEMO_SEED_DIR=str(tmp_path / "seed"),
        DEMO_ORIGIN="https://demo.test",
        NORMAL_ORIGIN="https://normal.test",
        DEMO_SESSION_SECRET=SECRETS[0],
        DEMO_CSRF_SECRET=SECRETS[1],
        SESSION_SECRET=SECRETS[2],
        DEMO_REQUESTS_PER_MINUTE="600",
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
    assert not any(secret in output for secret in SECRETS), output


def test_http_visitors_are_isolated_through_mutation_forgery_and_reset(
    tmp_path,
):
    run_case("isolation", tmp_path)


def test_http_delete_and_csv_export_stay_private_and_skip_deleted_rows(
    tmp_path,
):
    from test_s4_csv import ADMIN_COLUMNS, NEW_COLUMNS

    run_case(
        "delete_export",
        tmp_path,
        EXPECTED_HEADER=json.dumps(ADMIN_COLUMNS + NEW_COLUMNS + SUFFIX),
    )


def test_conversation_analysis_and_run_ledger_stay_private_per_visitor(
    tmp_path,
):
    """§28: A's turns, analysis and GenerationRun/CallEvent rows are absent in B."""
    run_case("run_ledger", tmp_path)


def test_teacher_csv_has_prefix_20_and_demo_suffix_5(tmp_path):
    """§23: /sessions/{id}/export.csv adds the 5 suffix columns after the 20."""
    from test_s4_csv import NEW_COLUMNS, TEACHER_COLUMNS

    run_case(
        "teacher_csv",
        tmp_path,
        EXPECTED_HEADER=json.dumps(TEACHER_COLUMNS + NEW_COLUMNS + SUFFIX),
    )


# --------------------------------------------------------------------------
# Subprocess cases.


async def post(client, path, ns, **kwargs):
    return await client.post(path, params=ns, headers=headers(client), **kwargs)


async def lookup(app, client):
    """The visitor's current generation file and (visitor_id, generation)."""
    visitor = await visitor_of(app, client)
    snapshot = await app.state.demo_request_db.visitors.snapshot(visitor)
    return snapshot.ref.path, snapshot.scope


def refused(response, *, status, expected):
    """A rejected request: safe envelope, no-store, none of A's data."""
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert code(response) == expected
    for leak in ("A-renamed", "A-only", "데모 교사", "데모 관리자"):
        assert leak not in response.text, response.text


async def case_isolation(tmp):
    from src import main

    app = main.app
    await real_seed(tmp)
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        a, b = client_for(app, "10.0.0.1"), client_for(app, "10.0.0.2")
        sa, sb = await start(a, "admin"), await start(b, "admin")
        ns_a, ns_b = namespace(sa), namespace(sb)
        assert ns_a["ds"] != ns_b["ds"]
        path_a, scope_a = await lookup(app, a)
        path_b, scope_b = await lookup(app, b)
        assert path_a != path_b and scope_a[0] != scope_b[0]

        async def observe_http(client, ns, path):
            state = (await client.get("/demo/state")).json()
            pages = {
                page: await client.get(page, params=ns)
                for page in ("/admin/users", "/admin/groups", "/metrics")
            }
            assert all(r.status_code == 200 for r in pages.values()), pages
            # demo-config's sliding idle expiry moves with every request (§8).
            html = {
                name: re.sub(
                    r'"expires_at": "[^"]*"', '"expires_at": ""', r.text
                )
                for name, r in pages.items()
            }
            html["/metrics"] = pages["/metrics"].json()["database"]  # no uptime
            return (
                {k: state[k] for k in sa if k != "expires_at"},
                html,
                product_rows(path),
            )

        seed_b = await observe_http(b, ns_b, path_b)
        seed_a = await observe_http(a, ns_a, path_a)
        # Same seed: the same integer IDs on both sides, teacher id 2.
        assert seed_a[2] == seed_b[2]
        assert seed_a[2]["users"] == [
            (1, "데모 관리자"),
            (2, "데모 교사"),
        ]
        assert seed_a[2]["groups"] == [(1, "예시 그룹")]

        # A mutates: rename, create user and group, delete the teacher.
        r = await post(
            a, "/admin/users/2/update", ns_a, json={"nickname": "A-renamed"}
        )
        assert r.status_code == 200, r.text
        r = await post(
            a,
            "/admin/users",
            ns_a,
            json={
                "username": "a_only",
                "password": "password123",
                "nickname": "A-only",
            },
        )
        assert r.status_code == 201, r.text
        r = await post(a, "/admin/groups", ns_a, json={"name": "A-only group"})
        assert r.status_code == 201, r.text
        r = await post(a, "/admin/users/2/delete", ns_a)
        assert r.status_code == 200, r.text
        mutated = product_rows(path_a)
        assert [u[0] for u in mutated["users"]] == [1, 3]
        assert "A-only" in (await a.get("/admin/users", params=ns_a)).text

        async def assert_b_untouched(label):
            assert await observe_http(b, ns_b, path_b) == seed_b, label
            assert visitors.lease_count(scope_b) == 0, label

        await assert_b_untouched("after A mutations")

        # §10: A's namespace on B's cookie is a mapping mismatch, never A's data.
        stale = dict(status=409, expected="DEMO_STALE_GENERATION")
        for path in ("/admin/users", "/admin/groups", "/metrics"):
            refused(await b.get(path, params=ns_a), **stale)
            refused(
                await b.get(path, headers={"x-demo-scope": ns_a["ds"]}), **stale
            )
        refused(
            await post(
                b, "/admin/users/2/update", ns_a, json={"nickname": "from-B"}
            ),
            **stale,
        )
        r = await b.get(
            "/admin/users", params=ns_a, headers={"accept": "text/html"}
        )
        assert r.status_code == 409 and r.headers["cache-control"] == "no-store"
        assert "A-renamed" not in r.text and "A-only" not in r.text
        await assert_b_untouched("after namespace mismatch")

        # Forged or tampered session cookies never reach any visitor's data.
        genuine = b.cookies.get("demo_session_id")
        for value in ("forged-" + uuid4().hex, genuine[:-2] + "xx"):
            stranger = client_for(app, "10.0.0.3")
            stranger.cookies.set("demo_session_id", value, "demo.test")
            for ns in (ns_a, ns_b):
                refused(
                    await stranger.get("/admin/users", params=ns),
                    status=401,
                    expected="DEMO_SESSION_REQUIRED",
                )
        await assert_b_untouched("after forged cookie")

        # §3: DB/visitor/user selectors from the client are invalid input.
        selectors = {
            "visitor_id": scope_a[0],
            "user_id": "1",
            "db_path": str(path_a),
            "database_url": "sqlite+aiosqlite:///" + str(path_a),
        }
        for name, value in selectors.items():
            for request in (
                b.get("/admin/users", params={**ns_b, name: value}),
                b.get(
                    "/metrics",
                    params=ns_b,
                    headers={"x-demo-" + name.replace("_", "-"): value},
                ),
            ):
                r = await request
                refused(r, status=422, expected="DEMO_INVALID_REQUEST")
                assert str(path_a) not in r.text and scope_a[0] not in r.text
                assert visitors.lease_count(scope_b) == 0
        await assert_b_untouched("after selector injection")

        # A resets: new generation, old namespace stale, seed restored; B intact.
        r = await post(
            a,
            "/demo/reset",
            {},
            json={
                "request_id": str(uuid4()),
                "expected_generation": sa["generation"],
                "expected_auth_epoch": sa["auth_epoch"],
                "confirm": True,
            },
        )
        assert r.status_code in (200, 202), r.text
        new_a = await wait_ready(a, sa["generation"] + 1)
        refused(await a.get("/admin/users", params=ns_a), **stale)
        page = await a.get("/admin/users", params=namespace(new_a))
        assert page.status_code == 200
        assert "데모 교사" in page.text
        assert "A-renamed" not in page.text and "A-only" not in page.text
        path_new, scope_new = await lookup(app, a)
        assert product_rows(path_new) == seed_a[2]
        assert scope_new == (scope_a[0], sa["generation"] + 1)
        await assert_b_untouched("after A reset")
        assert path_b.exists() and path_b.name in visitor_files(tmp / "demo")


async def case_delete_export(tmp):
    from src import main

    app = main.app
    expected_header = json.loads(os.environ["EXPECTED_HEADER"])
    await real_seed(tmp)
    async with app.router.lifespan_context(app):
        a, b = client_for(app, "10.0.0.1"), client_for(app, "10.0.0.2")
        # A makes its second ended session through the real teacher flow.
        sa, sb = await start(a, "teacher"), await start(b, "admin")
        seed_sid = app.state.demo_request_db.seed_refs["completed_session"]
        new_sid, _ = await teacher_run(app, a, sa)
        assert new_sid != seed_sid
        r = await post(
            a,
            "/demo/role",
            {},
            json={
                "request_id": str(uuid4()),
                "role": "admin",
                "expected_generation": sa["generation"],
                "expected_auth_epoch": sa["auth_epoch"],
            },
        )
        assert r.status_code == 200, r.text
        sa = (await a.get("/demo/state")).json()
        ns_a, ns_b = namespace(sa), namespace(sb)
        path_a, _ = await lookup(app, a)
        path_b, _ = await lookup(app, b)
        sids = [seed_sid, new_sid]
        ids = {str(seed_sid), str(new_sid)}

        async def exports(client, ns, sids):
            return {
                "all": await client.get("/admin/sessions/export", params=ns),
                "selected": await post(
                    client,
                    "/admin/sessions/export-selected",
                    ns,
                    data={"session_ids": sids},
                ),
                "session-1": await client.get(
                    f"/admin/sessions/{seed_sid}/download", params=ns
                ),
            }

        def parse(response, target):
            assert response.status_code == 200, response.text
            assert response.headers["content-disposition"] == (
                f"attachment; filename=demo_d0-02-v1_admin_{target}.csv"
            )
            reader = csv.DictReader(
                io.StringIO(response.content.decode("utf-8-sig"))
            )
            rows = list(reader)
            assert (
                reader.fieldnames == expected_header
                and len(expected_header) == 32
            )
            return rows

        b_before = {
            k: v.content
            for k, v in (await exports(b, ns_b, [seed_sid])).items()
        }
        b_rows = product_rows(path_b)
        # B is the untouched seed: only the completed seed session.
        assert [s[0] for s in b_rows["sessions"]] == [seed_sid]
        seed_messages = b_rows["messages"]
        assert len(seed_messages) == 5
        a_rows = product_rows(path_a)
        assert [s[0] for s in a_rows["sessions"]] == sids
        assert a_rows["messages"][: len(seed_messages)] == seed_messages
        assert len(a_rows["messages"]) == len(seed_messages) + 5
        # Text that only the new session carries (the seed session is distinct).
        seed_texts = set(contents(path_a, seed_sid))
        new_texts = [
            t for t in contents(path_a, new_sid) if t not in seed_texts
        ]
        assert new_texts

        r = await post(
            a, "/admin/users/2/update", ns_a, json={"nickname": "A-renamed"}
        )
        assert r.status_code == 200, r.text
        rows = parse((await exports(a, ns_a, sids))["all"], "all")
        assert {r["session_id"] for r in rows} == ids
        assert {r["teacher_nickname"] for r in rows} == {"A-renamed"}

        # §22: ended sessions lose the teacher link but keep their content.
        r = await post(a, "/admin/users/2/delete", ns_a)
        assert r.status_code == 200, r.text
        after = product_rows(path_a)
        assert [s[:2] for s in after["sessions"]] == [
            (seed_sid, None),
            (new_sid, None),
        ]
        assert after["messages"] == a_rows["messages"]
        got = await exports(a, ns_a, sids)
        for target, name in (
            ("all", "all"),
            ("selected", "selected"),
            ("session-1", "session-1"),
        ):
            rows = parse(got[target], name)
            seen = {r["session_id"] for r in rows}
            assert seen == ({str(seed_sid)} if target == "session-1" else ids)
            for row in rows:
                assert row["teacher_id"] == row["teacher_username"] == ""
                assert row["teacher_nickname"] == ""
                assert row["demo_response_source"] == "prepared_example"
                assert row["demo_external_ai_called"] == "false"
                assert row["demo_content_version"] == "d0-02-v1"
                assert row["demo_fixture_version"] == "demo-fixture-v1"
                assert row["demo_record_origin"] == (
                    "seed_prepared"
                    if row["session_id"] == str(seed_sid)
                    else "visitor_generated"
                )
        # The deleted user is gone: its per-user export cannot reveal content.
        r = await a.get("/admin/user-conversations/2/export", params=ns_a)
        assert r.status_code == 404 and code(r) == "DEMO_RESOURCE_NOT_FOUND"
        assert not any(t in r.text for t in new_texts + list(seed_texts))

        # Soft delete hides the session from every export variant.
        r = await post(a, f"/admin/sessions/{new_sid}/delete", ns_a)
        assert r.status_code == 200, r.text
        assert product_rows(path_a)["sessions"][1][2] is not None
        got = await exports(a, ns_a, sids)
        for target, name in (
            ("all", "all"),
            ("selected", "selected"),
            ("session-1", "session-1"),
        ):
            rows = parse(got[target], name)
            assert {r["session_id"] for r in rows} == {str(seed_sid)}
            assert not any(t in got[target].text for t in new_texts)
        r = await a.get(f"/admin/sessions/{new_sid}/download", params=ns_a)
        assert r.status_code == 404 and code(r) == "DEMO_RESOURCE_NOT_FOUND"

        # B is byte-identical to its pre-change export and never sees A's edits.
        after_b = await exports(b, ns_b, [seed_sid])
        assert {k: v.content for k, v in after_b.items()} == b_before
        assert product_rows(path_b) == b_rows
        rows = parse(after_b["all"], "all")
        assert {r["session_id"] for r in rows} == {str(seed_sid)}
        assert {r["teacher_nickname"] for r in rows} == {"데모 교사"}


async def real_seed(tmp):
    """The #197 product seed from its own CLI; main.demo_seed loads it."""
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.demo.seed",
            "build",
            "--out",
            str(tmp / "seed"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr


def sse(response):
    """(event, data) frames; a run must end on exactly one terminal frame."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    assert out[-1][0] == "output.completed", out[-1]
    assert out[-1][1]["status"] == "completed", out[-1]
    return out


async def teacher_run(app, client, state):
    """Two completed student turns, one manual mentor run, end + analysis.

    Returns the session id and its run ids (student, student, mentor, analysis).
    """
    from src.demo.seed import INPUTS

    ns = namespace(state)
    scenario = app.state.demo_request_db.seed_refs["scenario"]
    r = await post(client, "/sessions", ns, json={"scenario_id": scenario})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    runs = []
    for line in (INPUTS["A"], INPUTS["common"]):
        r = await post(
            client,
            f"/sessions/{sid}/turns/stream",
            ns,
            json={"request_id": str(uuid4()), "content": line},
        )
        first = sse(r)[0][1]
        runs.append(first["run_id"])
        turn = first["turn_id"]
    r = await post(
        client,
        f"/sessions/{sid}/turns/{turn}/mentor/stream",
        ns,
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    runs.append(sse(r)[0][1]["run_id"])
    r = await post(
        client, f"/sessions/{sid}/end", ns, json={"request_id": str(uuid4())}
    )
    assert r.status_code == 202, r.text
    analysis = r.json()["run_id"]
    # The analysis task signals its registry token when it has settled.
    scope = (await lookup(app, client))[1]
    token = app.state.demo_request_db.lifecycle.lookup(
        scope, "analysis", analysis
    )
    # No token left means the analysis already settled.
    if token is not None:
        await asyncio.wait_for(token.task_control.done.wait(), 60)
    r = await client.get(f"/sessions/{sid}/analysis", params=ns)
    assert r.status_code == 200, r.text
    return sid, runs + [analysis]


async def case_run_ledger(tmp):
    from src import main

    app = main.app
    await real_seed(tmp)
    async with app.router.lifespan_context(app):
        visitors = app.state.demo_request_db.visitors
        a, b = client_for(app, "10.0.0.1"), client_for(app, "10.0.0.2")
        sa, sb = await start(a, "teacher"), await start(b, "teacher")
        ns_a, ns_b = namespace(sa), namespace(sb)
        path_a, _ = await lookup(app, a)
        path_b, scope_b = await lookup(app, b)
        seed_b = ledger_rows(path_b)
        assert ledger_rows(path_a) == seed_b

        sid, runs = await teacher_run(app, a, sa)
        mine = ledger_rows(path_a)
        for table in LEDGER_TABLES:
            assert len(mine[table]) > len(seed_b[table]), table
        assert {r[0] for r in mine["generation_run"]} >= set(runs)
        # A's own reads work, so B's refusals below are isolation, not typos.
        for path in (f"/runs/{runs[0]}", f"/sessions/{sid}/export.csv"):
            assert (await a.get(path, params=ns_a)).status_code == 200, path
        texts = contents(path_a, sid)
        assert len(texts) == 5, texts  # 2 teacher + 2 student + 1 tutor
        assert ledger_rows(path_b) == seed_b

        # §28: the same IDs and A's run UUIDs mean nothing to B; no body text.
        for path in (
            f"/sessions/{sid}/analysis",
            f"/sessions/{sid}/export.csv",
            f"/sessions/{sid}/analysis/runs/{runs[-1]}",
            *(f"/runs/{run}" for run in runs),
        ):
            r = await b.get(path, params=ns_b)
            refused(r, status=404, expected="DEMO_RESOURCE_NOT_FOUND")
            assert not any(t in r.text for t in texts), path
        assert ledger_rows(path_b) == seed_b
        assert visitors.lease_count(scope_b) == 0


async def case_teacher_csv(tmp):
    from src import main

    app = main.app
    header = json.loads(os.environ["EXPECTED_HEADER"])
    await real_seed(tmp)
    async with app.router.lifespan_context(app):
        a = client_for(app, "10.0.0.1")
        state = await start(a, "teacher")
        ns = namespace(state)

        async def export(sid, origin):
            r = await a.get(f"/sessions/{sid}/export.csv", params=ns)
            assert r.status_code == 200, r.text
            assert r.headers["content-disposition"] == (
                f"attachment; filename=demo_d0-02-v1_teacher_session-{sid}.csv"
            )
            reader = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))
            rows = list(reader)
            # 15 base + 5 analysis columns, then the 5 demo suffix columns.
            assert reader.fieldnames == header and len(header) == 25
            assert rows
            for row in rows:
                assert row["session_id"] == str(sid)
                assert row["demo_response_source"] == "prepared_example"
                assert row["demo_external_ai_called"] == "false"
                assert row["demo_content_version"] == "d0-02-v1"
                assert row["demo_fixture_version"] == "demo-fixture-v1"
                assert row["demo_record_origin"] == origin
            return rows

        seed_sid = app.state.demo_request_db.seed_refs["completed_session"]
        await export(seed_sid, "seed_prepared")

        sid, _ = await teacher_run(app, a, state)
        rows = await export(sid, "visitor_generated")
        roles = Counter(row["role"] for row in rows)
        assert roles == {
            "teacher": 2,
            "student": 2,
            "tutor": 1,
            "summary": 1,
        }, roles
        (summary,) = [row for row in rows if row["role"] == "summary"]
        assert summary["analysis_schema_version"] == "2"
        assert summary["analysis_status"] == "ok"
        for column in ("analysis_coverage_json", "misconception_findings_json"):
            json.loads(summary[column])


if __name__ == "__main__":
    import asyncio

    case, tmp = sys.argv[1], Path(sys.argv[2])

    async def run():
        try:
            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())

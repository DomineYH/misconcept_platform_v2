"""D3-02 on the real stack: CLI-built product seed, real load_seed, real
visitor DBs, session and CSRF middleware. No stand-ins or overrides."""

import asyncio
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from test_demo_d1_04_app import SECRETS, demo_env

ROOT = Path(__file__).resolve().parents[1]
# Visitor-only rows copied from the seed session; must never leak.
MARKER = "VISITOR-ONLY "


def run_real(case, tmp_path):
    # Each case sends more than the default 60 requests a minute.
    env = demo_env(tmp_path, DEMO_REQUESTS_PER_MINUTE="600")
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.demo.seed",
            "build",
            "--out",
            str(tmp_path / "seed"),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [sys.executable, __file__, case, str(tmp_path)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not any(secret in output for secret in SECRETS), output
    assert str(tmp_path) not in output, output


@pytest.mark.parametrize("case", ["guards", "exports"])
def test_demo_d3_02_on_real_seed_and_middleware(case, tmp_path):
    run_real(case, tmp_path)


# --------------------------------------------------------------------------
# Subprocess cases (APP_MODE=demo, TESTING=false, real product seed).


def real_app():
    from src import main
    from src.demo import seed

    app = main.app
    manifest = json.loads(seed.MANIFEST.read_text())
    return app, seed, manifest


async def check_real_seed(app, seed, manifest):
    # demo_seed() -> load_seed(DEMO_SEED_DIR) -> validate_seed, no stand-in.
    seed_dir = Path(os.environ["DEMO_SEED_DIR"])
    assert app.state.demo_seed.path == seed_dir / seed.SEED_NAME
    assert app.state.demo_request_db.seed_refs == manifest["seed_refs"]
    assert manifest["contract_version"] == "d0-03-v1-r3"


async def case_guards(tmp):
    from test_demo_d1_04_app import client_for, code, headers, start
    from test_demo_d3_02_guards import (
        ACTION,
        API_KEY,
        CHILD,
        CMP,
        CONV,
        FORBIDDEN,
        NEW,
        PROVENANCE,
        STOP_ALL,
        STOP_PANEL,
        Spies,
        call,
        check_error,
        counts,
        visitor_rows,
    )

    app, seed, manifest = real_app()
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    groups = "SELECT COUNT(*) FROM user_group"
    async with app.router.lifespan_context(app):
        await check_real_seed(app, seed, manifest)
        client = client_for(app)
        state = await start(client, "admin")
        before, groups_before = counts(root), visitor_rows(root, groups)
        good = headers(client)

        # CSRF middleware runs before the demo guard and before any CRUD.
        for bad in (
            {"origin": good["origin"]},
            {**good, "x-csrf-token": "forged"},
            {**good, "origin": "https://normal.test"},
            {"x-csrf-token": good["x-csrf-token"]},
        ):
            for path, body in (
                ("/admin/groups", {"name": "csrf"}),
                FORBIDDEN[0],
            ):
                response = await client.post(
                    path,
                    json=body,
                    headers=bad,
                    params={
                        "ds": state["browser_scope_id"],
                        "dg": state["generation"],
                        "da": state["auth_epoch"],
                    },
                )
                assert response.status_code == 403, response.text
                assert code(response) == "DEMO_CSRF_INVALID", response.text

        # §20: every refused POST, with an empty and a plausible body.
        for path, body in FORBIDDEN:
            for sent in ({}, body):
                response = await call(client, state, "POST", path, sent)
                check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
                assert API_KEY not in response.text

        # §20 reads come from this visitor's DB only.
        response = await call(client, state, "GET", "/admin/ai/state")
        assert response.status_code == 200, response.text
        assert response.json()["provenance"] == PROVENANCE
        for path in ("/admin/comparisons", "/admin/comparison-conversations"):
            response = await call(client, state, "GET", path)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            assert (body if isinstance(body, list) else body["items"]) == []
        for path in (f"/admin/ai/probes/{NEW}", CMP, CONV):
            response = await call(client, state, "GET", path)
            check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")
        # Stops/deletes only reach this visitor's rows; none exist yet.
        for method, path, body in (
            ("POST", f"/admin/ai/probes/{NEW}/cancel", {}),
            ("POST", f"{CMP}/stop", STOP_ALL),
            ("POST", f"{CMP}/panels/a/stop", STOP_PANEL),
            ("POST", f"{CONV}/stop", {**ACTION, "child_id": CHILD, **STOP_ALL}),
            ("DELETE", CMP, None),
            ("DELETE", CONV, None),
        ):
            response = await call(client, state, method, path, body)
            check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")
        assert counts(root) == before
        assert visitor_rows(root, groups) == groups_before

        # Control: the same CSRF headers do pass for allowed CRUD.
        response = await call(
            client, state, "POST", "/admin/groups", {"name": "Real stack"}
        )
        assert response.status_code == 201, response.text
        assert visitor_rows(root, groups) == [(groups_before[0][0] + 1,)]

        # A teacher keeps the existing admin refusal.
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
        response = await call(client, state, "POST", *FORBIDDEN[0])
        check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        response = await call(client, state, "GET", "/admin/ai/state")
        check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        spies.clean()


def copy_visitor_session(path, seed_id):
    """A visitor-generated session: the seed session's rows under a new id."""
    with closing(sqlite3.connect(path)) as db, db:
        columns = [
            row[1] for row in db.execute('PRAGMA table_info("session")')
        ][1:]
        db.execute(
            f'INSERT INTO "session"({",".join(columns)}) '
            f'SELECT {",".join(columns)} FROM "session" WHERE id=?',
            (seed_id,),
        )
        (new,) = db.execute("SELECT MAX(id) FROM session").fetchone()
        db.execute(
            "INSERT INTO message(session_id, role, content, created_at) "
            "SELECT ?, role, ? || content, created_at FROM message "
            "WHERE session_id=? ORDER BY id",
            (new, MARKER, seed_id),
        )
    return new


def one(path, sql, params=()):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return db.execute(sql, params).fetchall()


def csv_rows(response, name, seed_id):
    """§23: 27 + 5 columns, demo filename, suffix values per row."""
    from test_s4_csv import ADMIN_COLUMNS, NEW_COLUMNS

    from src.services.export import DEMO_COLUMNS

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    value = response.headers["content-disposition"]
    assert value == f"attachment; filename=demo_d0-02-v1_admin_{name}.csv"
    assert value.isascii()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    rows = list(reader)
    assert len(ADMIN_COLUMNS + NEW_COLUMNS) == 27
    assert reader.fieldnames == ADMIN_COLUMNS + NEW_COLUMNS + DEMO_COLUMNS
    for row in rows:
        assert None not in row and None not in row.values(), row
        assert row["demo_response_source"] == "prepared_example"
        assert row["demo_external_ai_called"] == "false"
        assert row["demo_content_version"] == "d0-02-v1"
        assert row["demo_fixture_version"] == "demo-fixture-v1"
        assert row["demo_record_origin"] == (
            "seed_prepared"
            if row["session_id"] == str(seed_id)
            else "visitor_generated"
        )
    return rows


def sessions(rows):
    return {int(row["session_id"]) for row in rows}


def save(name, response):
    # ponytail: evidence samples only when asked; tests never depend on them.
    out = os.environ.get("D3_02_EVIDENCE_DIR")
    if out:
        Path(out, name).write_bytes(response.content)


async def case_exports(tmp):
    from test_demo_d1_04_app import (
        client_for,
        headers,
        namespace,
        start,
        visitor_files,
    )
    from test_demo_d3_02_guards import Spies, call, check_error

    app, seed, manifest = real_app()
    spies = Spies()
    root = Path(os.environ["DEMO_DATA_ROOT"])
    async with app.router.lifespan_context(app):
        await check_real_seed(app, seed, manifest)
        refs = app.state.demo_request_db.seed_refs
        seed_id, teacher = refs["completed_session"], refs["teacher"]
        a = client_for(app, "10.0.0.1")
        state_a = await start(a, "admin")
        (name_a,) = visitor_files(root)
        db_a = root / name_a
        visitor = copy_visitor_session(db_a, seed_id)
        b = client_for(app, "10.0.0.2")
        state_b = await start(b, "admin")
        (name_b,) = set(visitor_files(root)) - {name_a}
        db_b = root / name_b

        async def get(client, state, path, **kwargs):
            return await client.get(path, params=namespace(state), **kwargs)

        async def selected(ids, request_id=None):
            extra = (
                {}
                if request_id is None
                else {"x-demo-export-request-id": request_id}
            )
            return await a.post(
                "/admin/sessions/export-selected",
                data={"session_ids": ids},
                params=namespace(state_a),
                headers=headers(a) | extra,
            )

        # Single download of the seed session: 6 rows, echo headers.
        request_id = str(uuid4())
        response = await get(
            a,
            state_a,
            f"/admin/sessions/{seed_id}/download",
            headers={"x-demo-export-request-id": request_id},
        )
        rows = csv_rows(response, f"session-{seed_id}", seed_id)
        assert Counter(row["role"] for row in rows) == {
            "teacher": 2,
            "student": 2,
            "tutor": 1,
            "summary": 1,
        }
        assert response.headers["x-demo-export-request-id"] == request_id
        assert response.headers["x-demo-export-row-count"] == "6"
        save(f"single_session-{seed_id}.csv", response)

        # Selected, all and per-user exports carry both origins.
        request_id = str(uuid4())
        response = await selected([seed_id, visitor], request_id)
        rows = csv_rows(response, "selected", seed_id)
        assert sessions(rows) == {seed_id, visitor}
        assert response.headers["x-demo-export-request-id"] == request_id
        assert response.headers["x-demo-export-row-count"] == str(len(rows))
        save("selected_seed_and_visitor.csv", response)
        for path, name in (
            ("/admin/sessions/export", "all"),
            (f"/admin/user-conversations/{teacher}/export", f"user-{teacher}"),
        ):
            response = await get(a, state_a, path)
            assert sessions(csv_rows(response, name, seed_id)) == {
                seed_id,
                visitor,
            }
            assert not [k for k in response.headers if k.startswith("x-demo")]

        # The other visitor never sees A's rows.
        response = await get(b, state_b, "/admin/sessions/export")
        assert sessions(csv_rows(response, "all", seed_id)) == {seed_id}
        assert MARKER not in response.text
        save("other_visitor_all.csv", response)
        response = await get(b, state_b, f"/admin/sessions/{visitor}/download")
        check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")

        # §22/§24.1: session soft delete.
        response = await call(
            a, state_a, "POST", f"/admin/sessions/{visitor}/delete"
        )
        assert response.status_code == 200, response.text
        response = await call(
            a, state_a, "POST", f"/admin/sessions/{visitor}/delete"
        )
        assert response.status_code == 400, response.text
        for path in (
            f"/admin/sessions/{visitor}/download",
            f"/admin/sessions/{visitor}/analysis",
        ):
            check_error(
                await get(a, state_a, path), 404, "DEMO_RESOURCE_NOT_FOUND"
            )
        # The modal stays readable as a tombstone, without the body.
        response = await get(a, state_a, f"/admin/sessions/{visitor}/detail")
        assert response.status_code == 200, response.text
        assert MARKER not in response.text
        for path, name in (
            ("/admin/sessions/export", "all"),
            (f"/admin/user-conversations/{teacher}/export", f"user-{teacher}"),
        ):
            response = await get(a, state_a, path)
            assert sessions(csv_rows(response, name, seed_id)) == {seed_id}
            assert MARKER not in response.text
        response = await selected([seed_id, visitor])
        assert sessions(csv_rows(response, "selected", seed_id)) == {seed_id}
        assert MARKER not in response.text
        response = await selected([visitor])
        assert MARKER not in response.text
        assert not response.content.startswith(b"\xef\xbb\xbf")
        # Preserved, not erased: the row is tombstoned in A's DB only.
        assert one(
            db_a,
            "SELECT deleted_at IS NOT NULL FROM session WHERE id=?",
            (visitor,),
        ) == [(1,)]

        # §22/§24.1: teacher delete keeps ended sessions, unlinked.
        response = await call(
            a, state_a, "POST", f"/admin/users/{teacher}/delete"
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted", "user_id": teacher}
        assert one(db_a, "SELECT id, teacher_id FROM session ORDER BY id") == [
            (seed_id, None),
            (visitor, None),
        ]
        assert one(
            db_a, "SELECT COUNT(*) FROM message WHERE session_id=?", (seed_id,)
        ) == [(5,)]
        response = await get(
            a, state_a, f"/admin/user-conversations/{teacher}/export"
        )
        check_error(response, 404, "DEMO_RESOURCE_NOT_FOUND")
        response = await get(a, state_a, "/admin/sessions/export")
        rows = csv_rows(response, "all", seed_id)
        assert sessions(rows) == {seed_id} and len(rows) == 6
        assert {row["teacher_id"] for row in rows} == {""}
        save("after_deletes_all.csv", response)

        facts = (
            await a.get("/demo/state", params={"include_tour": "true"})
        ).json()["tour_facts"]
        assert facts["teacher_deleted"] is True, facts
        assert facts["session_deleted"] is True, facts
        assert facts["journey_session_id"] == visitor, facts

        # B is untouched by A's deletes.
        assert one(db_b, "SELECT teacher_id FROM session") == [(teacher,)]
        facts = (
            await b.get("/demo/state", params={"include_tour": "true"})
        ).json()["tour_facts"]
        assert not facts["teacher_deleted"] and not facts["session_deleted"]
        response = await get(b, state_b, "/admin/sessions/export")
        rows = csv_rows(response, "all", seed_id)
        assert {row["teacher_id"] for row in rows} == {str(teacher)}
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

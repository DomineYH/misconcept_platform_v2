"""D2-04 G3 (#198): the #197 seed in the real demo app, egress 0 (§27 G3).

Each case runs in its own process, like test_demo_d1_04_app: the real
FastAPI app in APP_MODE=demo, real session/CSRF middleware, real visitor DBs
cloned from a seed built by `python -m src.demo.seed build`. The SDK
constructors, httpx/requests transports and outbound sockets are spied from
before startup until after shutdown and cleanup.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_demo_d1_04_app import SECRETS, demo_env

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = dict(
    mode="demo",
    response_source="prepared_example",
    external_ai_called=False,
    contract_version="d0-03-v1-r3",
    content_version="d0-02-v1",
    fixture_version="demo-fixture-v1",
    record_origin="visitor_generated",
)
USAGE = (
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "pricing_as_of",
    "pricing_source",
    "raw_usage_json",
)


@pytest.fixture(scope="module")
def seed_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("g3")
    built = subprocess.run(
        [sys.executable, "-m", "src.demo.seed", "build", "--out", "seed"],
        env=demo_env(tmp),
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    receipt = json.loads(built.stdout.strip().splitlines()[-1])
    assert receipt == json.loads((tmp / "seed/receipt.json").read_text())
    return tmp / "seed"


def run_case(case, seed_dir, tmp_path):
    env = demo_env(tmp_path)
    env.update(
        DEMO_SEED_DIR=str(seed_dir),
        # Cleanup runs only when a case asks, so retired DBs can be read.
        DEMO_CLEANUP_INTERVAL_SECONDS="300",
    )
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


def test_g3_flow_on_real_seed(seed_dir, tmp_path):
    run_case("flow", seed_dir, tmp_path)


def test_g3_reset_during_stream(seed_dir, tmp_path):
    run_case("reset", seed_dir, tmp_path)


def test_g3_close_during_stream_cancels_it(seed_dir, tmp_path):
    run_case("close", seed_dir, tmp_path)


def test_g3_analyze_route_on_real_seed(seed_dir, tmp_path):
    run_case("analyze", seed_dir, tmp_path)


class Egress:
    """Records and refuses every attempt to leave the process.

    Only AF_UNIX sockets reach the real socket functions, so not even the
    canary below makes an outbound connection.
    """

    def __init__(self):
        import socket

        import anthropic
        import httpx
        import openai
        import requests
        from google import genai

        self.attempts = []
        record = self.attempts.append

        def guarded(name, real):
            def call(sock, address):
                if sock.family == socket.AF_UNIX:
                    return real(sock, address)
                record((name, address))
                raise OSError("egress blocked")

            return call

        def blocked(name):
            def call(*args, **kwargs):
                record((name,))
                raise OSError("egress blocked")

            return call

        def blocked_async(name):
            async def call(*args, **kwargs):
                record((name,))
                raise OSError("egress blocked")

            return call

        socket.socket.connect = guarded("connect", socket.socket.connect)
        socket.socket.connect_ex = guarded(
            "connect_ex", socket.socket.connect_ex
        )
        socket.create_connection = blocked("create_connection")
        httpx.HTTPTransport.handle_request = blocked("httpx")
        httpx.AsyncHTTPTransport.handle_async_request = blocked_async(
            "httpx_async"
        )
        requests.adapters.HTTPAdapter.send = blocked("requests")
        for name, owner in self.sdks(openai, anthropic, genai):
            owner.__init__ = blocked(name)

    @staticmethod
    def sdks(openai, anthropic, genai):
        return (
            ("OpenAI", openai.OpenAI),
            ("AsyncOpenAI", openai.AsyncOpenAI),
            ("Anthropic", anthropic.Anthropic),
            ("AsyncAnthropic", anthropic.AsyncAnthropic),
            ("genai.Client", genai.Client),
        )

    def canary(self):
        """Every installed hook fires once and refuses; then the log clears."""
        import asyncio
        import socket

        import anthropic
        import httpx
        import openai
        import requests
        from google import genai

        address = ("192.0.2.1", 9)

        async def async_httpx():
            async with httpx.AsyncClient() as client:
                await client.get("http://192.0.2.1")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probes = [
            ("connect", lambda: sock.connect(address)),
            ("connect_ex", lambda: sock.connect_ex(address)),
            ("create_connection", lambda: socket.create_connection(address)),
            ("httpx", lambda: httpx.get("http://192.0.2.1")),
            ("httpx_async", lambda: asyncio.run(async_httpx())),
            ("requests", lambda: requests.get("http://192.0.2.1", timeout=1)),
            *(
                (name, lambda owner=owner: owner(api_key="unused"))
                for name, owner in self.sdks(openai, anthropic, genai)
            ),
        ]
        try:
            for name, probe in probes:
                before = len(self.attempts)
                try:
                    probe()
                except OSError:
                    pass
                else:
                    raise AssertionError(f"{name} was not refused")
                assert [a[0] for a in self.attempts[before:]] == [name], (
                    name,
                    self.attempts,
                )
        finally:
            sock.close()
        self.attempts.clear()


def parse_sse(text):
    frames = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def visitor_db(root, generation=None):
    from test_demo_d1_04_app import visitor_files

    names = [
        name
        for name in visitor_files(root)
        if generation is None or f"-g{generation}-" in name
    ]
    return [root / name for name in names]


def rows(path, sql, params=()):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(sql, params)]
    finally:
        db.close()


async def as_teacher(client, state):
    from uuid import uuid4

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
    assert state["role"] == "teacher", state
    return state


async def new_session(client, state, scenario):
    from test_demo_d3_02_guards import call

    response = await call(
        client, state, "POST", "/sessions", {"scenario_id": scenario}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def student_turn(client, state, session_id, content):
    from uuid import uuid4

    from test_demo_d3_02_guards import call

    response = await call(
        client,
        state,
        "POST",
        f"/sessions/{session_id}/turns/stream",
        {"request_id": str(uuid4()), "content": content},
    )
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def case_flow(tmp):
    from uuid import uuid4

    from test_demo_d3_02_guards import FORBIDDEN, call, check_error

    from src import main
    from src.demo import seed

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    manifest = json.loads(seed.MANIFEST.read_text())
    refs = manifest["seed_refs"]
    async with app.router.lifespan_context(app):
        assert EGRESS.attempts == [], EGRESS.attempts  # startup
        # §14/§20: direct catalog/probe/admin AI requests stop at the guard.
        admin = client_for(app, "10.0.0.2")
        admin_state = await start(admin, "admin")
        model = refs["model"]
        for path, body in [
            *FORBIDDEN,
            (f"/admin/ai/models/{model}/probes", {"request_id": str(uuid4())}),
        ]:
            response = await call(admin, admin_state, "POST", path, body)
            check_error(response, 403, "DEMO_ACTION_FORBIDDEN")
        (admin_db,) = visitor_db(root)
        assert (
            rows(
                admin_db,
                "SELECT COUNT(*) AS n FROM model_probe UNION ALL "
                "SELECT COUNT(*) FROM comparison_run UNION ALL "
                "SELECT COUNT(*) FROM api_usage_log WHERE provider != 'demo' "
                "OR operation IN ('model_list', 'probe', 'comparison')",
            )
            == [{"n": 0}] * 3
        )

        client = client_for(app, "10.0.0.1")
        state = await as_teacher(client, await start(client, "admin"))
        (teacher_db,) = [p for p in visitor_db(root) if p != admin_db]
        session_id = await new_session(client, state, refs["scenario"])

        frames = await student_turn(client, state, session_id, seed.INPUTS["A"])
        kinds = [kind for kind, _ in frames]
        assert kinds[-1] == "output.completed", frames
        assert "output.delta" in kinds, kinds
        student = frames[-1][1]
        assert student["provenance"] == PROVENANCE, student
        turn_id, student_run = student["turn_id"], student["run_id"]

        response = await call(
            client,
            state,
            "POST",
            f"/sessions/{session_id}/turns/{turn_id}/mentor/stream",
            {"request_id": str(uuid4()), "trigger": "manual"},
        )
        assert response.status_code == 200, response.text
        mentor = parse_sse(response.text)
        assert mentor[-1][0] == "output.completed", mentor
        mentor_run = mentor[-1][1]["run_id"]

        for run_id in (student_run, mentor_run):
            response = await call(client, state, "GET", f"/runs/{run_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "completed", body
            assert body["provenance"] == PROVENANCE, body

        response = await call(
            client,
            state,
            "POST",
            f"/sessions/{session_id}/end",
            {"request_id": str(uuid4())},
        )
        # A single-call plan needs no confirmation: end reserves the run.
        assert response.status_code == 202, response.text
        analysis_run = response.json()["run_id"]
        await analysis_done(client, state, session_id, analysis_run)

        # §6: final saves are in this visitor's DB only.
        runs = rows(
            teacher_db,
            "SELECT id, operation, status FROM generation_run "
            "WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )
        assert {run["id"]: run["status"] for run in runs} == {
            student_run: "completed",
            mentor_run: "completed",
            analysis_run: "ok",
        }, runs
        messages = rows(
            teacher_db,
            "SELECT role, generation_run_id FROM message WHERE session_id = ?",
            (session_id,),
        )
        assert {m["generation_run_id"] for m in messages} >= {
            student_run,
            mentor_run,
        }, messages
        assert rows(
            teacher_db,
            "SELECT ended_at IS NOT NULL AS ended FROM session WHERE id = ?",
            (session_id,),
        ) == [{"ended": 1}]
        assert rows(
            admin_db,
            "SELECT COUNT(*) AS n FROM generation_run WHERE id IN (?, ?, ?)",
            (student_run, mentor_run, analysis_run),
        ) == [{"n": 0}]

        assert_ledger(teacher_db, student_run, mentor_run, analysis_run)
        assert EGRESS.attempts == [], EGRESS.attempts

        await app.state.demo_request_db.visitors.cleanup_tick()
    assert EGRESS.attempts == [], EGRESS.attempts  # shutdown


def hold_first_delta(count):
    """Holds each real demo stream after its first delta until released.

    `streaming` is set once `count` streams (one per visitor scope) wait.
    """
    import asyncio

    from src.demo.provider import DemoProvider

    streaming, release, held_scopes = asyncio.Event(), asyncio.Event(), []
    stream_text = DemoProvider.stream_text

    async def held(self, request, *args, **kwargs):
        async for event in stream_text(self, request, *args, **kwargs):
            yield event
            scope = request.demo_context.scope
            if event.type == "text_delta" and scope not in held_scopes:
                held_scopes.append(scope)
                if len(held_scopes) == count:
                    streaming.set()
                await release.wait()

    DemoProvider.stream_text = held
    return streaming, release


async def held_pair(app, root, scenario, content, streaming):
    """Visitors A and B, each with a held student stream in session N."""
    import asyncio
    from types import SimpleNamespace

    pair = []
    for ip in ("10.0.0.1", "10.0.0.2"):
        client = client_for(app, ip)
        before = set(visitor_db(root))
        state = await as_teacher(client, await start(client, "admin"))
        (db,) = set(visitor_db(root)) - before
        session_id = await new_session(client, state, scenario)
        pair.append(
            SimpleNamespace(client=client, state=state, db=db, id=session_id)
        )
    # §27 C05: the same resource ID lives in both visitor scopes.
    assert pair[0].id == pair[1].id, pair
    for visitor in pair:
        visitor.turn = asyncio.create_task(
            student_turn(visitor.client, visitor.state, visitor.id, content)
        )
    await asyncio.wait_for(streaming.wait(), 30)
    return pair


def student_runs(visitor):
    return rows(
        visitor.db,
        "SELECT id, status FROM generation_run WHERE session_id = ?",
        (visitor.id,),
    )


async def finishes_untouched(b, release):
    """B was not cancelled by A's action and completes once released."""
    import asyncio

    await asyncio.sleep(0.5)
    assert not b.turn.done(), b.turn
    assert [run["status"] for run in student_runs(b)] == ["running"]
    release.set()
    frames = await asyncio.wait_for(b.turn, 30)
    assert frames[-1][0] == "output.completed", frames
    assert [run["status"] for run in student_runs(b)] == ["completed"]


def assert_no_response_content(frames):
    """The terminal carries only status fields, never the response body."""
    terminal = frames[-1][1]
    assert not {"text", "partial_text", "content", "delta"} & terminal.keys()
    # `message` is the fixed error text here, not the student message.
    assert not isinstance(terminal.get("message"), dict), terminal
    sent = [data["text"] for kind, data in frames if kind == "output.delta"]
    encoded = json.dumps(terminal, ensure_ascii=False)
    assert sent and not any(text in encoded for text in sent), terminal


async def analysis_done(client, state, session_id, run_id):
    import asyncio

    from test_demo_d3_02_guards import call

    for _ in range(400):
        response = await call(
            client,
            state,
            "GET",
            f"/sessions/{session_id}/analysis/runs/{run_id}",
        )
        assert response.status_code == 200, response.text
        latest = response.json()["latest_run"]
        if latest["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.05)
    assert latest["status"] == "ok" and latest["adopted"], response.text
    assert latest["outcome"]["provenance"] == PROVENANCE, latest
    response = await call(
        client, state, "GET", f"/sessions/{session_id}/analysis"
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"] == PROVENANCE, response.text


def assert_ledger(db, *run_ids):
    """§19: one attempt-1 row per call, unknown usage stays null."""
    ledger = rows(
        db,
        "SELECT * FROM api_usage_log WHERE run_id IN "
        f"({','.join('?' * len(run_ids))})",
        run_ids,
    )
    assert sorted((row["run_id"], row["attempt_no"]) for row in ledger) == (
        sorted((run, 1) for run in run_ids)
    ), ledger
    for row in ledger:
        assert row["provider"] == "demo", row
        assert row["status"] == "completed", row
        assert row["finished_at"] is not None, row
        assert row["usage_complete"] == 0, row
        assert all(row[name] is None for name in USAGE), row


async def case_analyze(tmp):
    from uuid import uuid4

    from test_demo_d3_02_guards import call

    from src import main
    from src.demo import seed

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    refs = json.loads(seed.MANIFEST.read_text())["seed_refs"]
    async with app.router.lifespan_context(app):
        client = client_for(app, "10.0.0.1")
        state = await as_teacher(client, await start(client, "admin"))
        (db,) = visitor_db(root)
        session_id = await new_session(client, state, refs["scenario"])
        frames = await student_turn(client, state, session_id, seed.INPUTS["A"])
        assert frames[-1][0] == "output.completed", frames
        turn_request = frames[0][1]["request_id"]
        response = await call(
            client, state, "POST", f"/sessions/{session_id}/close"
        )
        assert response.status_code == 200, response.text

        path = f"/sessions/{session_id}/analyze"
        body = {"request_id": str(uuid4())}
        response = await call(client, state, "POST", path, body)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await analysis_done(client, state, session_id, run_id)
        assert_ledger(db, run_id)

        def counts():
            return rows(
                db,
                "SELECT COUNT(*) AS n FROM generation_run UNION ALL "
                "SELECT COUNT(*) FROM api_usage_log",
            )

        before = counts()
        # §16: a replay returns the same run; another body conflicts.
        response = await call(client, state, "POST", path, body)
        assert response.status_code in (200, 202), response.text
        assert response.json()["run_id"] == run_id, response.text
        # The student turn's request_id names another body (§16).
        response = await call(
            client, state, "POST", path, {"request_id": turn_request}
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail == dict(
            code="DEMO_REQUEST_CONFLICT",
            retryable=False,
            recovery="demo.refresh_state",
            generation=state["generation"],
            auth_epoch=state["auth_epoch"],
        ), detail
        assert counts() == before
        assert EGRESS.attempts == [], EGRESS.attempts
    assert EGRESS.attempts == [], EGRESS.attempts


async def case_close(tmp):
    import asyncio

    from test_demo_d3_02_guards import call

    from src import main
    from src.demo import seed

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    refs = json.loads(seed.MANIFEST.read_text())["seed_refs"]
    streaming, release = hold_first_delta(2)
    async with app.router.lifespan_context(app):
        a, b = await held_pair(
            app, root, refs["scenario"], seed.INPUTS["A"], streaming
        )
        response = await call(
            a.client, a.state, "POST", f"/sessions/{a.id}/close"
        )
        assert response.status_code == 200, response.text
        assert response.json()["already_ended"] is False, response.text
        try:
            # §6: only the visitor-scope cancel can end A's held stream.
            frames = await asyncio.wait_for(asyncio.shield(a.turn), 10)
            await finishes_untouched(b, release)
        finally:
            release.set()
        assert frames[-1][0] != "output.completed", frames
        assert frames[-1][0].startswith("run."), frames
        (run,) = student_runs(a)
        assert run["status"] == "cancelled", run
        assert rows(
            a.db,
            "SELECT COUNT(*) AS n FROM message WHERE generation_run_id = ?",
            (run["id"],),
        ) == [{"n": 0}]
        assert EGRESS.attempts == [], EGRESS.attempts
    assert EGRESS.attempts == [], EGRESS.attempts


async def case_reset(tmp):
    import asyncio
    from uuid import uuid4

    from test_demo_d1_04_app import headers

    from src import main
    from src.demo import seed

    app = main.app
    root = Path(os.environ["DEMO_DATA_ROOT"])
    refs = json.loads(seed.MANIFEST.read_text())["seed_refs"]
    streaming, release = hold_first_delta(2)
    async with app.router.lifespan_context(app):
        a, b = await held_pair(
            app, root, refs["scenario"], seed.INPUTS["A"], streaming
        )
        generation = a.state["generation"]
        (run,) = student_runs(a)
        response = await a.client.post(
            "/demo/reset",
            json={
                "request_id": str(uuid4()),
                "expected_generation": generation,
                "expected_auth_epoch": a.state["auth_epoch"],
                "confirm": True,
            },
            headers=headers(a.client),
        )
        assert response.status_code in (200, 202), response.text
        try:
            # Reset cancels A's scope only; B stays held until released.
            frames = await asyncio.wait_for(asyncio.shield(a.turn), 30)
            await finishes_untouched(b, release)
        finally:
            release.set()
        # Post-SSE: the stale stream ends without a completed frame and
        # its terminal carries no response content.
        kind = frames[-1][0]
        assert kind != "output.completed" and kind.startswith("run."), frames
        assert_no_response_content(frames)
        for _ in range(200):
            state = (await a.client.get("/demo/state")).json()
            if state["state"] == "ready":
                break
            await asyncio.sleep(0.05)
        assert state["generation"] == generation + 1, state

        # §6: the late completion/interruption save was refused.
        assert student_runs(a) == [run], run
        assert rows(
            a.db,
            "SELECT COUNT(*) AS n FROM message WHERE generation_run_id = ?",
            (run["id"],),
        ) == [{"n": 0}]
        assert rows(
            a.db,
            "SELECT COUNT(*) AS n FROM api_usage_log "
            "WHERE run_id = ? AND finished_at IS NOT NULL",
            (run["id"],),
        ) == [{"n": 0}]
        (new_db,) = set(visitor_db(root, generation + 1)) - {b.db}
        assert rows(
            new_db,
            "SELECT COUNT(*) AS n FROM generation_run WHERE id = ?",
            (run["id"],),
        ) == [{"n": 0}]

        visitors = app.state.demo_request_db.visitors
        for _ in range(100):
            await visitors.cleanup_tick()
            if not a.db.exists():
                break
            await asyncio.sleep(0.05)
        assert not a.db.exists()
        # The old DB is not recreated; B's DB is untouched by A's reset.
        assert set(visitor_db(root)) == {new_db, b.db}
        assert EGRESS.attempts == [], EGRESS.attempts
    assert not a.db.exists()
    assert EGRESS.attempts == [], EGRESS.attempts


if __name__ == "__main__":
    import asyncio

    case, tmp = sys.argv[1], Path(sys.argv[2])
    sys.path.insert(0, str(Path(__file__).parent))
    # Before the app is imported: startup is inside the spied window.
    EGRESS = Egress()
    EGRESS.canary()
    from test_demo_d1_04_app import CLIENTS, client_for, start

    async def run():
        try:
            await globals()["case_" + case](tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run())
    assert EGRESS.attempts == [], EGRESS.attempts
    print(case, "egress", len(EGRESS.attempts))

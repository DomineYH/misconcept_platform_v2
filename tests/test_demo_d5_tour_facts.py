"""D5: every demo page's `demo_config` carries the visit's TourFacts (§24)."""

import json
import re
import subprocess
import sys
from pathlib import Path

from test_demo_d1_04_app import (
    CLIENTS,
    client_for,
    demo_env,
    namespace,
    start,
)

ROOT = Path(__file__).resolve().parents[1]


def test_demo_page_config_carries_tour_facts(tmp_path):
    built = subprocess.run(
        [sys.executable, "-m", "src.demo.seed", "build"]
        + ["--out", str(tmp_path / "seed")],
        env=demo_env(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [sys.executable, __file__, str(tmp_path)],
        env=demo_env(tmp_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_normal_mode_demo_config_is_unchanged():
    from types import SimpleNamespace

    from src.api.dependencies import demo_template_context
    from src.config import config

    request = SimpleNamespace(state=SimpleNamespace())
    assert demo_template_context(request) == {
        "demo_config": {
            "mode": "normal",
            "public_url": config.DEMO_PUBLIC_URL or None,
        }
    }


def clone_journey(db_path, seed_session):
    """Stage a visitor journey: an ended copy of the seed's analysed session."""
    import sqlite3

    def remap(column):
        return f"'d5' || substr({column}, 3)"

    with sqlite3.connect(db_path) as db:
        new = db.execute('SELECT max(id) + 1 FROM "session"').fetchone()[0]
        db.execute(
            'INSERT INTO "session" SELECT :new, scenario_id, teacher_id, '
            "started_at, ended_at, deleted_at, tutor_intervention_count, "
            "tutor_question_count, config_snapshot_json, config_hash, "
            "source_scenario_version, snapshot_origin, snapshot_created_at "
            'FROM "session" WHERE id=:old',
            {"new": new, "old": seed_session},
        )
        columns = [
            row[1] for row in db.execute("PRAGMA table_info(generation_run)")
        ]
        values = {
            "id": remap("id"),
            "turn_id": remap("turn_id"),
            "request_id": remap("request_id"),
            "session_id": ":new",
            "accepted_report_id": "NULL",
        }
        db.execute(
            f"INSERT INTO generation_run ({', '.join(columns)}) SELECT "
            + ", ".join(values.get(c, c) for c in columns)
            + " FROM generation_run WHERE session_id=:old",
            {"new": new, "old": seed_session},
        )
        columns = [row[1] for row in db.execute("PRAGMA table_info(message)")]
        values = {
            "id": "NULL",
            "session_id": ":new",
            "turn_id": remap("turn_id"),
            "generation_run_id": remap("generation_run_id"),
        }
        db.execute(
            f"INSERT INTO message ({', '.join(columns)}) SELECT "
            + ", ".join(values.get(c, c) for c in columns)
            + " FROM message WHERE session_id=:old ORDER BY id",
            {"new": new, "old": seed_session},
        )
        run, payload = db.execute(
            "SELECT run.id, report.payload_json FROM session_feedback_report "
            "AS report JOIN generation_run AS run ON run.accepted_report_id="
            "report.id WHERE report.session_id=:old",
            {"old": seed_session},
        ).fetchone()
        payload = json.loads(payload)
        payload["metadata"]["run_id"] = "d5" + run[2:]
        report = db.execute(
            "INSERT INTO session_feedback_report (session_id, version, model, "
            "prompt_hash, status, payload_json, created_at) SELECT :new, "
            "version, model, prompt_hash, status, :payload, created_at FROM "
            "session_feedback_report WHERE session_id=:old",
            {"new": new, "old": seed_session, "payload": json.dumps(payload)},
        ).lastrowid
        db.execute(
            "UPDATE generation_run SET accepted_report_id=:report "
            "WHERE id=:run",
            {"report": report, "run": "d5" + run[2:]},
        )
        db.execute(
            "INSERT INTO session_summary (session_id, distribution_json, "
            "feedback, created_at) SELECT :new, distribution_json, feedback, "
            "created_at FROM session_summary WHERE session_id=:old",
            {"new": new, "old": seed_session},
        )
    db.close()
    return new


def page_config(response):
    assert response.status_code == 200, response.text
    match = re.search(
        r'<script type="application/json" id="demo-config">(.*?)</script>',
        response.text,
        re.S,
    )
    assert match, response.text[:500]
    return json.loads(match.group(1))


async def case_tour_facts(tmp):
    from src import main

    app = main.app
    root = tmp / "demo"
    async with app.router.lifespan_context(app):
        client = client_for(app)
        state = await start(client, "teacher")
        params = namespace(state)
        before = page_config(
            await client.get(
                "/scenarios", params=params, headers={"accept": "text/html"}
            )
        )["tour_facts"]
        assert before is not None and before["journey_session_id"] is None
        assert before["teacher_entered"] is True

        (visit,) = root.glob("*-g*-*.db")
        refs = app.state.demo_session.tour_content.seed_refs
        journey = clone_journey(visit, refs["completed_session"])

        # The page is read first, so its facts come from display() alone.
        config = page_config(
            await client.get(
                "/scenarios", params=params, headers={"accept": "text/html"}
            )
        )
        response = await client.get("/demo/state?include_tour=true")
        assert response.status_code == 200, response.text
        facts = response.json()["tour_facts"]
        assert facts["journey_session_id"] == journey, facts
        assert facts["analysis_ready"] is True, facts
        assert config["tour_facts"] == facts
        assert config["generation"] == facts["generation"]


if __name__ == "__main__":
    import asyncio

    async def run(tmp):
        try:
            await case_tour_facts(tmp)
        finally:
            for client in CLIENTS:
                await client.aclose()

    asyncio.run(run(Path(sys.argv[1])))

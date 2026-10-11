"""Isolated screens: python tests/browser_server.py (no DB/LLM)."""

import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(
    TESTING="true",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    OPENAI_API_KEY="test",
)
from src.api.dependencies import templates  # noqa: E402
from tests.s2_screen_fixtures import editor_fixture  # noqa: E402
from tests.s4_screen_fixtures import analysis_fixture  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path)
        query = parse_qs(path.query)
        if path.path == "/fixtures/s4/result":
            body = json.dumps(
                analysis_fixture(
                    query.get("state", ["ok"])[0], admin="admin" in query
                )
            ).encode()
            mime = "application/json"
        elif path.path == "/fixtures/s4/history":
            body = (
                templates.get_template("admin/sessions.html")
                .render(
                    user=SimpleNamespace(nickname="Admin", role="admin"),
                    sessions=[
                        SimpleNamespace(
                            id=1,
                            teacher=None,
                            summary=True,
                            started_at=datetime(2026, 10, 1),
                            ended_at=datetime(2026, 10, 1, 10),
                        )
                    ],
                    teachers=[],
                    total_pages=1,
                    session_display=lambda session: dict(
                        scenario_title="분수의 크기 비교",
                        snapshot_provenance=dict(snapshot_origin="native"),
                    ),
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/fixtures/s4/modal":
            body = (
                templates.get_template("partials/analysis_modal.html")
                .render(
                    is_admin=True,
                    session_id=1,
                    analysis_result_url=f"/fixtures/s4/result?{path.query}",
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/fixtures/s4/analysis":
            body = (
                templates.get_template("analysis.html")
                .render(
                    user=SimpleNamespace(
                        nickname="Admin" if "admin" in query else "Teacher",
                        role="admin" if "admin" in query else "teacher",
                    ),
                    session_id=1,
                    distribution={},
                    grade_counts={},
                    analysis_result_url=f"/fixtures/s4/result?{path.query}",
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/fixtures/s2/editor":
            body = (
                templates.get_template("admin/scenario_editor.html")
                .render(
                    user=SimpleNamespace(nickname="Admin", role="admin"),
                    editor=editor_fixture(
                        new="new" in query,
                        published="published" in query,
                        legacy=query.get("legacy", [""])[0],
                    ),
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/fixtures/s2/analysis":
            enabled = "off" not in query
            body = (
                templates.get_template("analysis.html")
                .render(
                    user=SimpleNamespace(nickname="Teacher", role="teacher"),
                    session_id=1,
                    feedback="Narrative feedback",
                    feedback_status="ok",
                    classification_enabled=enabled,
                    label_names={"stable_1": "Frozen display name"},
                    distribution={"stable_1": 1} if enabled else {},
                    framework_label_criteria={},
                    grade_counts={"우수": 1 if enabled else 0, "개선": 0},
                    stats=dict(
                        duration_seconds=60,
                        teacher_question_count=1,
                        student_response_count=1,
                        tutor_intervention_count=0,
                    ),
                    messages=[
                        dict(
                            role="teacher",
                            content="Why?",
                            turn_index=1,
                            level="high" if enabled else None,
                        )
                    ],
                    questions=(
                        [
                            dict(
                                content="Why?",
                                label="stable_1",
                                label_name="Frozen display name",
                                grade="우수",
                                reasoning=dict(summary="Classified reason"),
                            )
                        ]
                        if enabled
                        else []
                    ),
                    feedback_sections=dict(
                        brief_feedback=["Narrative feedback"],
                        strengths=[
                            dict(quote="Why?", reason="Narrative strength")
                        ],
                        improvements=[
                            dict(
                                student_quote="Two thirds",
                                missed_reason="Narrative improvement",
                                alternative_question="What about halves?",
                                alternative_reason="Compare parts",
                            )
                        ],
                        dialogue_coaching=[],
                    ),
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path in {"/chat", "/scenarios", "/admin/scenarios-page"}:
            scenario = SimpleNamespace(
                id=1,
                title="Browser regression",
                student_name="Student",
                student_profile="Profile",
                subject="Math",
                problem_situation=(
                    " " if "missing_problem" in query else "Problem"
                ),
                greeting_message="Hello",
                mentor_mode=(
                    ("manual" if "mentor_manual" in query else "auto")
                    if any(key.startswith("mentor") for key in query)
                    else "off"
                ),
                mentor_name="멘토",
                prompt="PRIVATE STUDENT PROMPT",
                video_url="https://www.youtube.com/watch?v=legacy-secret",
                video_transcript="PRIVATE LEGACY TRANSCRIPT",
                is_active=1,
                status="draft",
                config_version=1,
                config_json={"problem": {"public_text": "Problem"}},
                review_required=False,
            )
            template = {
                "/chat": "chat.html",
                "/scenarios": "scenarios.html",
                "/admin/scenarios-page": "admin/scenarios.html",
            }[path.path]
            messages = []
            if "mentor_history" in query:
                messages = [
                    SimpleNamespace(
                        id=message_id,
                        role=role,
                        content=content,
                        created_at=None,
                        turn_id="turn-1",
                    )
                    for message_id, role, content in [
                        (10, "teacher", "Teacher question"),
                        (11, "student", "Stored student"),
                    ]
                ]
            if "mentor_coached" in query:
                messages.append(
                    SimpleNamespace(
                        id=12,
                        role="tutor",
                        content="Stored coaching",
                        created_at=None,
                        turn_id="turn-1",
                    )
                )
            if "mentor_legacy" in query:
                messages = [
                    SimpleNamespace(
                        id=message_id,
                        role=role,
                        content=content,
                        created_at=None,
                        turn_id=None,
                    )
                    for message_id, role, content in [
                        (7, "teacher", "Legacy teacher"),
                        (8, "student", "Legacy student"),
                        (9, "tutor", "Legacy mentor"),
                        (10, "teacher", "Legacy unanswered teacher"),
                    ]
                ]
            body = (
                templates.get_template(template)
                .render(
                    scenario=scenario,
                    scenarios=[scenario],
                    session_id=1,
                    student_name="Student",
                    user=SimpleNamespace(
                        nickname="Tester",
                        role=(
                            "admin"
                            if path.path.startswith("/admin")
                            else "teacher"
                        ),
                    ),
                    messages=messages,
                    session_ended="ended" in query,
                    groups=[],
                    scenario_group_map={},
                    session_counts={1: 0},
                )
                .encode()
            )
            if "admin_history" in query:
                history = [
                    dict(role=role, content=content, turn_index=turn_index)
                    for role, content, turn_index in [
                        ("tutor", "Legacy coaching", None),
                        ("teacher", "First question", 1),
                        ("student", "First answer", 1),
                        ("teacher", "Second question", 2),
                        ("student", "Second answer", 2),
                        ("tutor", "Late coaching <script>unsafe()</script>", 1),
                    ]
                ]
                modal = templates.get_template(
                    "partials/analysis_modal.html"
                ).render(
                    is_admin=True,
                    session_id=1,
                    feedback="Preserved feedback",
                    messages=history,
                    questions=[],
                    distribution={},
                    framework_label_criteria={},
                )
                body = body.replace(
                    b"</body>",
                    b'<div id="history-fixture">'
                    + modal.encode()
                    + b"</div></body>",
                )
            if "stream" in parse_qs(path.query, keep_blank_values=True):
                script = (
                    b'<script type="module">import {mountStudentStream} '
                    b'from "/static/js/student-stream.js"; '
                    b"mountStudentStream(window.chatUI);</script>"
                )
                if "mentor" in query:
                    script = script.replace(
                        b"mountStudentStream(window.chatUI);",
                        b"import {mountMentorStream} "
                        b'from "/static/js/mentor-stream.js"; '
                        b"mountMentorStream(window.chatUI);"
                        b"mountStudentStream(window.chatUI);",
                    )
                body = body.replace(b"</body>", script + b"</body>")
            mime = "text/html; charset=utf-8"
        elif path.path == "/admin/api-usage":
            fixture = json.loads(
                Path("tests/fixtures/api_usage.json").read_text()
            )
            body = (
                templates.get_template("admin/api_usage.html")
                .render(
                    user=SimpleNamespace(nickname="Admin", role="admin"),
                    **fixture,
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/admin/ai":
            body = (
                templates.get_template("admin/ai.html")
                .render(user=SimpleNamespace(nickname="Admin", role="admin"))
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/admin/users":
            body = (
                templates.get_template("admin/users.html")
                .render(
                    user=SimpleNamespace(nickname="Admin", role="admin"),
                    users=[],
                    groups=[],
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path.startswith("/static/"):
            target = Path(path.path[1:]).resolve()
            if (
                not target.is_relative_to(Path("static").resolve())
                or not target.is_file()
            ):
                self.send_error(404)
                return
            body = target.read_bytes()
            mime = "text/css" if target.suffix == ".css" else "text/javascript"
        else:
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("BROWSER_TEST_PORT", "8765"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

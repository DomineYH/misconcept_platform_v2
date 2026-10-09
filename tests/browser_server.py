"""Isolated screens: python tests/browser_server.py (no DB/LLM)."""

import json
import os
import sys
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
from tests.s2_screen_fixtures import (
    editor_fixture,
    lesson_fixture,
)  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path)
        query = parse_qs(path.query)
        if path.path == "/fixtures/s2/lesson":
            body = (
                templates.env.from_string(
                    Path("tests/fixtures/s2_lesson.html").read_text()
                )
                .render(
                    user=SimpleNamespace(nickname="Teacher", role="teacher"),
                    lesson=lesson_fixture(query),
                )
                .encode()
            )
            mime = "text/html; charset=utf-8"
        elif path.path == "/fixtures/s2/lesson-controller.js":
            body = Path("tests/fixtures/s2_lesson_controller.js").read_bytes()
            mime = "text/javascript"
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
        elif path.path in {"/chat", "/scenarios", "/admin/scenarios-page"}:
            framework = SimpleNamespace(id=1, name="Framework")
            student_template = SimpleNamespace(
                id=1, template_name="Student template", version=1
            )
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
                mentor_mode="manual" if "mentor" in query else "off",
                mentor_name="멘토",
                prompt="PRIVATE STUDENT PROMPT",
                video_url="https://www.youtube.com/watch?v=legacy-secret",
                video_transcript="PRIVATE LEGACY TRANSCRIPT",
                is_active=1,
                framework_id=1,
                framework=framework,
                student_template_id=1,
                tutor_template_id=2 if "mentor" in query else None,
                tutor_template=None,
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
                    frameworks=[framework],
                    student_templates=[student_template],
                    tutor_templates=[],
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

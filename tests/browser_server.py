"""Isolated chat fixture server: python tests/browser_server.py (no DB/LLM)."""

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
from src.api.dependencies import templates


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/chat":
            scenario = SimpleNamespace(
                title="Browser regression",
                student_name="Student",
                student_profile="Profile",
                subject="Math",
                problem_situation="Problem",
                greeting_message="Hello",
                video_url=None,
                video_transcript=None,
            )
            body = (
                templates.get_template("chat.html")
                .render(
                    scenario=scenario,
                    session_id=1,
                    student_name="Student",
                    user=SimpleNamespace(nickname="Tester", role="teacher"),
                    messages=[],
                    session_ended="ended" in parse_qs(path.query),
                )
                .encode()
            )
            if "stream" in parse_qs(path.query, keep_blank_values=True):
                script = (
                    b'<script type="module">import {mountStudentStream} '
                    b'from "/static/js/student-stream.js"; '
                    b"mountStudentStream(window.chatUI);</script>"
                )
                body = body.replace(b"</body>", script + b"</body>")
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

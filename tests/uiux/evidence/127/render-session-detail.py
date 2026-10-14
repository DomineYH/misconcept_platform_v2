import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as S

sys.path.insert(0, str(Path.cwd()))
os.environ.update(
    TESTING="true",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    OPENAI_API_KEY="test-only",
)
from src.api.dependencies import templates  # noqa: E402

rows = []
for state in ["active", "ended", "deleted", "empty", "accepted", "legacy"]:
    messages = (
        []
        if state == "empty"
        else [
            S(
                id=i,
                role=role,
                turn_id="turn-1",
                content="긴 원래 대화 합성 내용\n" * 20,
                created_at=datetime(2026, 10, 1),
            )
            for i, role in enumerate(["teacher", "student", "tutor"], 1)
        ]
    )
    session = S(
        id=1,
        teacher=S(nickname="합성 교사", username="synthetic"),
        started_at=datetime(2026, 10, 1),
        ended_at=None if state == "active" else datetime(2026, 10, 1, 10),
        deleted_at=datetime(2026, 10, 1, 11) if state == "deleted" else None,
        messages=messages,
    )
    context = dict(
        user=S(nickname="Admin", role="admin"),
        session=session,
        scenario_title="긴 시나리오 " * 20,
        student_name="합성 학생",
        snapshot_provenance=dict(
            snapshot_origin=(
                "legacy_reconstructed" if state == "legacy" else "native"
            ),
            snapshot_created_at="2026-10-01",
            unknown_field_names=["당시 모델"],
        ),
    )
    if state == "accepted":
        context["analysis_result_url"] = "/fixtures/s4/result?state=preserved"
    html = templates.env.from_string(
        "{% extends 'layout.html' %}{% block content %}{% include 'partials/session_detail.html' %}{% endblock %}"
    ).render(**context)
    rows.append(dict(state=state, html=html))
Path(
    "/tmp/misconcept-uiux-120/127-logs/session-detail-fixtures.json"
).write_text(json.dumps(rows, ensure_ascii=False))

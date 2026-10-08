"""Phase A preserves historical readers and identifies late mentor targets."""

import re
from datetime import datetime, timedelta

from test_scenario_api import client as client_fixture
from test_scenario_api import login

from src.models import Message, SessionSummary

client = client_fixture


async def test_admin_history_identifies_late_mentor_without_guessing_legacy(
    data, client
):
    start = datetime(2026, 1, 1)
    rows = [
        ("tutor", "Legacy coaching", None, None),
        ("teacher", "First question", "turn-1", 1),
        ("student", "First answer", "turn-1", 1),
        ("teacher", "Second question", "turn-2", 2),
        ("student", "Second answer", "turn-2", 2),
        ("tutor", "Late coaching for first", "turn-1", 1),
    ]
    data.db.add_all(
        Message(
            session_id=data.session.id,
            role=role,
            content=content,
            turn_id=turn_id,
            turn_index=index,
            created_at=start + timedelta(seconds=offset),
        )
        for offset, (role, content, turn_id, index) in enumerate(rows)
    )
    data.db.add(
        SessionSummary(
            session_id=data.session.id,
            distribution_json='{"A": 2}',
            feedback="Preserved feedback",
        )
    )
    await data.db.commit()
    login(client, data.admin)
    modal = await client.get(
        f"/admin/sessions/{data.session.id}/analysis_modal"
    )
    assert modal.status_code == 200
    messages = re.findall(r'<li class="coach-msg[\s\S]*?</li>', modal.text)
    assert [
        re.search(r'<p class="coach-msg__content">(.*?)</p>', m)[1]
        for m in messages
    ] == [row[1] for row in rows]
    assert "coach-msg__turn" not in messages[0]
    assert '<span class="coach-msg__turn">1번째 턴</span>' in messages[-1]
    assert '<span class="coach-msg__turn">2번째 턴</span>' in messages[-2]

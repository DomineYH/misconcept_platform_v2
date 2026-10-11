from datetime import datetime

import pytest
from fastapi import HTTPException

from src.api.routes import (
    admin_session_actions,
    session_analysis,
)
from src.models import (
    Message,
    QuestionAnalysis,
    SessionSummary,
)


async def test_user_and_admin_analysis_contract(data):
    sid = data.session.id
    first = Message(
        session_id=sid,
        role="teacher",
        content="first",
        created_at=datetime(2026, 1, 1),
    )
    later = Message(
        session_id=sid,
        role="teacher",
        content="later",
        created_at=datetime(2026, 1, 1, 0, 1),
    )
    data.db.add_all(
        [
            later,
            first,
            SessionSummary(
                session_id=sid, distribution_json='{"A": 1}', feedback="legacy"
            ),
        ]
    )
    await data.db.flush()
    data.db.add(
        QuestionAnalysis(
            message_id=first.id,
            label="A",
            grade="우수",
            confidence=0.8,
            meta_json='{"reason": "test"}',
        )
    )
    await data.db.commit()
    normal = await session_analysis.get_analysis(sid, data.owner, data.db)
    admin = await admin_session_actions._load_analysis_response(sid, data.db)
    assert normal == admin
    assert set(normal) == {
        "distribution",
        "classification_enabled",
        "label_names",
        "feedback",
        "feedback_status",
        "retryable",
        "feedback_sections",
        "stats",
        "questions",
        "messages",
        "framework_label_criteria",
        "grade_counts",
        "session_ended_at",
        "scenario_title",
        "student_name",
        "snapshot_provenance",
        "accepted_report",
        "latest_run",
        "plan",
        "permissions",
        "actions",
    }
    assert normal["feedback_status"] == "legacy"
    assert normal["accepted_report"]["coverage"] is None
    assert normal["accepted_report"]["status"] == "legacy"
    assert normal["latest_run"] is None
    assert [q["content"] for q in normal["questions"]] == ["first", "later"]
    assert normal["questions"][1]["label"] == "Unclassified"
    assert normal["messages"][0]["level"] == "high"
    assert normal["grade_counts"] == {"우수": 1, "개선": 0}
    assert normal["stats"]["duration_seconds"] == 86400
    with pytest.raises(HTTPException) as denied:
        await session_analysis.get_analysis(sid, data.other, data.db)
    assert denied.value.status_code == 403

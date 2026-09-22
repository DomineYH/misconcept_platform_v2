from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from src.api.routes import admin_session_actions as admin_actions
from src.api.routes.session_helpers import load_session, validate_scenario_access
from src.models import Message, SessionSummary
from src.services.session_mgr import SessionManager


def request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


async def test_ownership_and_group_access(data):
    assert await load_session(data.session.id, data.owner, data.db) is data.session
    with pytest.raises(HTTPException) as denied:
        await load_session(data.session.id, data.other, data.db)
    assert denied.value.status_code == 403
    assert await validate_scenario_access(data.scenario.id, data.owner, data.db) is data.scenario
    with pytest.raises(HTTPException) as denied:
        await validate_scenario_access(data.scenario.id, data.other, data.db)
    assert denied.value.status_code == 403
    assert await validate_scenario_access(data.scenario.id, data.admin, data.db) is data.scenario
    data.scenario.mark_deleted()
    await data.db.flush()
    with pytest.raises(HTTPException) as denied:
        await validate_scenario_access(data.scenario.id, data.admin, data.db)
    assert denied.value.status_code == 404


async def test_teacher_message_survives_bot_failure(data):
    manager = SessionManager(data.db, data.session.id)
    manager.student_bot = AsyncMock()
    manager.student_bot.generate_response.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        await manager.process_teacher_message("Why?")
    await data.db.rollback()
    async with data.factory() as reader:
        messages = (await reader.scalars(select(Message))).all()
        assert [(m.role, m.content) for m in messages] == [("teacher", "Why?")]


async def test_regeneration_failure_preserves_summary(data, monkeypatch):
    sid = data.session.id
    data.db.add(SessionSummary(session_id=sid, distribution_json='{"A": 1}', feedback="original"))
    await data.db.commit()
    fake = AsyncMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(admin_actions, "run_llm_pipeline", fake)
    with pytest.raises(HTTPException) as failed:
        await admin_actions.regenerate_analysis(request(), sid, data.admin, data.db)
    assert failed.value.status_code == 500
    assert fake.await_count == 1
    async with data.factory() as reader:
        summary = (await reader.scalars(select(SessionSummary))).one()
        assert summary.feedback == "original"


def test_application_and_templates_compile():
    from src.main import app
    from src.api.dependencies import templates
    assert "/sessions/{session_id}/analyze" in app.openapi()["paths"]
    for name in templates.env.list_templates():
        templates.env.get_template(name)

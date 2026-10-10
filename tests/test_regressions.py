from unittest.mock import AsyncMock

import pytest
from analysis_test_helpers import call_analysis_route
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from src.api.routes import admin_session_actions as admin_actions
from src.api.routes.session_helpers import (
    load_session,
    validate_scenario_access,
)
from src.models import Message, Scenario, SessionSummary
from src.services.session_mgr import SessionManager


def request():
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []}
    )


async def test_ownership_and_group_access(data):
    data.scenario = await data.db.get(Scenario, data.scenario.id)
    assert (
        await load_session(data.session.id, data.owner, data.db) is data.session
    )
    with pytest.raises(HTTPException) as denied:
        await load_session(data.session.id, data.other, data.db)
    assert denied.value.status_code == 403
    assert (
        await validate_scenario_access(data.scenario.id, data.owner, data.db)
        is data.scenario
    )
    with pytest.raises(HTTPException) as denied:
        await validate_scenario_access(data.scenario.id, data.other, data.db)
    assert denied.value.status_code == 403
    assert (
        await validate_scenario_access(data.scenario.id, data.admin, data.db)
        is data.scenario
    )
    data.scenario.mark_deleted()
    await data.db.flush()
    with pytest.raises(HTTPException) as denied:
        await validate_scenario_access(data.scenario.id, data.admin, data.db)
    assert denied.value.status_code == 404


async def test_teacher_message_survives_bot_failure(data):
    manager = SessionManager(data.db, data.session.id)
    manager.student_bot = AsyncMock()
    manager.student_bot.prepare_response.return_value = (AsyncMock(), None)
    manager.student_bot.invoke_response.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        await manager.process_teacher_message("Why?")
    await data.db.rollback()
    async with data.factory() as reader:
        messages = (await reader.scalars(select(Message))).all()
        assert [(m.role, m.content) for m in messages] == [("teacher", "Why?")]


async def test_regeneration_failure_preserves_summary(data, monkeypatch):
    from analysis_fixtures import install_analysis_snapshot

    from src.services import analysis_pipeline

    await install_analysis_snapshot(data, monkeypatch)
    sid = data.session.id
    data.db.add(
        SessionSummary(
            session_id=sid, distribution_json='{"A": 1}', feedback="original"
        )
    )
    await data.db.commit()
    fake = AsyncMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(analysis_pipeline, "run_llm_pipeline", fake)
    result = await call_analysis_route(
        admin_actions.regenerate_analysis, request(), sid, data.admin, data.db
    )
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["adopted"] is False
    assert fake.await_count == 1
    async with data.factory() as reader:
        summary = (await reader.scalars(select(SessionSummary))).one()
        assert summary.feedback == "original"


def test_application_and_templates_compile():
    from src.api.dependencies import templates
    from src.main import app

    assert "/sessions/{session_id}/analyze" in app.openapi()["paths"]
    for name in templates.env.list_templates():
        templates.env.get_template(name)


async def test_http_permissions_retry_and_ended_message_guard(
    data, monkeypatch
):
    import base64
    import json

    import httpx
    from itsdangerous import TimestampSigner

    from src.api.dependencies import get_db_session
    from src.config import config
    from src.main import app
    from src.services import analysis_pipeline

    async def database():
        async with data.factory() as db:
            yield db
            await db.commit()

    def cookie(user):
        payload = base64.b64encode(json.dumps({"user_id": user.id}).encode())
        return TimestampSigner(config.SESSION_SECRET).sign(payload).decode()

    from analysis_fixtures import install_analysis_snapshot

    await install_analysis_snapshot(data, monkeypatch)

    sid = data.session.id
    await analysis_pipeline.create_fallback_summary(sid, ["A", "B"], data.db)
    fake = AsyncMock(
        return_value=(
            {"A": 1},
            [],
            {"brief_feedback": ["HTTP success"]},
            "ok",
            "test",
            "hash",
            [],
        )
    )
    monkeypatch.setattr(analysis_pipeline, "run_llm_pipeline", fake)
    app.dependency_overrides[get_db_session] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as raw_client:
            from analysis_test_helpers import AnalysisApi

            client = AnalysisApi(raw_client)
            assert (
                await client.get(f"/sessions/{sid}/analysis")
            ).status_code == 303
            client.cookies.set("session_id", cookie(data.other))
            assert (
                await client.post(f"/sessions/{sid}/analyze")
            ).status_code == 403
            assert (
                await client.post(f"/admin/sessions/{sid}/analyze_regenerate")
            ).status_code == 403
            client.cookies.clear()
            client.cookies.set("session_id", cookie(data.owner))
            result = await client.post(f"/sessions/{sid}/analyze")
            assert result.status_code == 200
            assert result.json()["feedback_status"] == "ok"
            late = await client.post(
                f"/sessions/{sid}/turns/stream",
                json={
                    "request_id": "00000000-0000-0000-0000-000000000001",
                    "content": "late",
                },
            )
            assert late.status_code == 400
            assert fake.await_count == 1
            normal = await client.get(f"/sessions/{sid}/analysis")
            assert normal.json()["feedback"] == "HTTP success"
            client.cookies.clear()
            client.cookies.set("session_id", cookie(data.admin))
            modal = await client.get(f"/admin/sessions/{sid}/analysis_modal")
            assert modal.status_code == 200
            assert (
                f'data-result-url="/admin/sessions/{sid}/analysis"'
                in modal.text
            )
            public = await client.get(f"/admin/sessions/{sid}/analysis")
            assert public.json()["accepted_report"]["brief_feedback"] == [
                "HTTP success"
            ]
    finally:
        app.dependency_overrides.clear()

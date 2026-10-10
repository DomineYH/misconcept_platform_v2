"""Context rejection follows authentication, ownership and active CSRF."""

from uuid import uuid4

import httpx
from sqlalchemy import select
from starlette_csrf import CSRFMiddleware
from test_scenario_api import login
from test_student_generation import client, scenario_payload, student

from src.config import config
from src.main import app
from src.models import ApiUsageLog, GenerationRun, Message
from src.services import context_budget
from src.services.model_capabilities import capabilities

__all__ = ["client", "scenario_payload", "student"]


async def test_context_limit_is_safe_only_after_ownership_and_csrf_checks(
    data, client, student, monkeypatch
):
    definition = capabilities("openai", "gpt-5-mini")
    definition["combined_context_tokens"] = 1600
    monkeypatch.setattr(context_budget, "capabilities", lambda *_: definition)
    protected = CSRFMiddleware(
        app,
        secret=config.SESSION_SECRET,
        cookie_name="csrftoken",
        header_name="x-csrf-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as api:
        payload = dict(request_id=str(uuid4()), content="Question 한🙂")
        url = f"/sessions/{data.session.id}/turns/stream"
        login(api, data.other)
        await api.get(f"/scenarios/{data.scenario.id}")
        denied = await api.post(
            url,
            json=payload,
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert denied.status_code == 403 and "context_limit" not in denied.text
        login(api, data.owner)
        await api.get(f"/scenarios/{data.scenario.id}")
        assert (await api.post(url, json=payload)).status_code == 403
        result = await api.post(
            url,
            json=payload,
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "context_limit"
        assert (
            "Student profile" not in result.text
            and "estimated_input_tokens" not in result.text
        )
    student.responses.create.assert_not_awaited()
    async with data.factory() as db:
        for model in (Message, GenerationRun, ApiUsageLog):
            assert (await db.scalars(select(model))).all() == []

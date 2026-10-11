"""Comparison authentication, input limits and zero-call preflight."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog, ComparisonRun, GenerationRun

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.mark.parametrize(
    "change",
    [
        "missing_problem",
        "history_order",
        "blank_message",
        "extra",
        "options",
        "too_many",
        "long_text",
        "unsupported_role",
        "many_panels",
    ],
)
async def test_invalid_comparison_is_zero_calls_and_zero_runs(
    data, client, student, comparison, change
):
    login(client, data.admin)
    if change == "missing_problem":
        comparison["source_snapshot"]["config"]["problem"]["public_text"] = ""
    elif change == "history_order":
        comparison["canonical_context"][0]["role"] = "student"
    elif change == "blank_message":
        comparison["current_message"] = "  "
    elif change == "extra":
        comparison["owner_id"] = data.other.id
    elif change == "options":
        comparison["panels"][0]["model_config"]["options"]["temperature"] = 0.5
    elif change == "too_many":
        comparison["canonical_context"] *= 100
    elif change == "long_text":
        comparison["current_message"] = "x" * 50001
    elif change == "unsupported_role":
        comparison["target_role"] = "analysis"
    elif change == "many_panels":
        comparison["panels"].append(
            {**comparison["panels"][0], "panel_id": "2"}
        )
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 422, response.text
    assert "PRIVATE ANALYSIS" not in response.text
    student.responses.create.assert_not_awaited()
    async with data.factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(ComparisonRun))
            == 0
        )
        assert (
            await db.scalar(select(func.count()).select_from(GenerationRun))
            == 0
        )
        assert (
            await db.scalar(select(func.count()).select_from(ApiUsageLog)) == 0
        )


async def test_comparison_access_expiry_and_body_limits(
    data, client, student, comparison
):
    import httpx
    from starlette_csrf import CSRFMiddleware

    from src.config import config
    from src.main import app
    from src.models import User

    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).status_code == 401
    login(client, data.owner)
    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).status_code == 403
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(response)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    second = User(username="second_admin", nickname="Second", role="admin")
    data.db.add(second)
    await data.db.commit()
    for actor, status in ((data.owner, 403), (second, 404)):
        login(client, actor)
        denied = await client.get(path)
        assert denied.status_code == status
        assert "PRIVATE ANALYSIS" not in denied.text
    login(client, data.admin)
    protected = CSRFMiddleware(
        app, secret=config.SESSION_SECRET, header_name="x-csrf-token"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as secured:
        login(secured, data.admin)
        await secured.get(path)
        assert (
            await secured.post(
                "/admin/comparisons",
                json={**comparison, "request_id": str(uuid4())},
            )
        ).status_code == 403
        secured.headers["x-csrf-token"] = secured.cookies["csrftoken"]
        assert (
            await secured.post("/admin/comparisons", json=comparison)
        ).status_code == 200
    assert (
        await client.post(
            "/admin/comparisons",
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
    ).status_code == 413
    assert (
        await client.post(
            "/admin/comparisons",
            content=b'{"bad":"\xff"}',
            headers={"Content-Type": "application/json"},
        )
    ).status_code == 422
    async with data.factory() as db:
        from datetime import timedelta

        run = await db.get(ComparisonRun, accepted["comparison_run_id"])
        assert run.expires_at - run.created_at == timedelta(days=30)
        run.expires_at = run.created_at - timedelta(seconds=1)
        await db.commit()
    assert (await client.get(path)).status_code == 404
    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).status_code == 404
    assert student.responses.create.await_count == 1


@pytest.mark.parametrize(
    "state", ["disabled", "unverified", "unknown", "budget", "admission"]
)
async def test_comparison_live_preflight_refuses_without_writes(
    data, client, student, comparison, state, monkeypatch
):
    from src.models import AppSetting
    from src.services import model_capabilities

    login(client, data.admin)
    expected = 503
    if state == "disabled":
        student.connection.enabled = False
    elif state == "unverified":
        student.model.verification_state = {}
    elif state == "unknown":
        monkeypatch.setattr(model_capabilities, "DEFINITIONS", {})
    elif state == "budget":
        # Valid bounded messages exceed the exact model budget as a whole.
        comparison["canonical_context"] = [
            dict(
                role="teacher" if i % 2 == 0 else "student",
                content="길" * 50000,
            )
            for i in range(10)
        ]
        expected = 422
    elif state == "admission":
        setting = await data.db.get(AppSetting, 1)
        setting.limits_json = {**setting.limits_json, "admin": 1}
        from src.services.call_admission import active_calls

        occupied = type("Occupied", (), {"provider": "openai", "admin": True})()
        active_calls.add(occupied)
        expected = 429
    await data.db.commit()
    try:
        response = await client.post("/admin/comparisons", json=comparison)
    finally:
        if state == "admission":
            active_calls.discard(occupied)
    assert response.status_code == expected, response.text
    if state == "admission":
        assert response.headers["Retry-After"] == "1"
    student.responses.create.assert_not_awaited()
    async with data.factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(ComparisonRun))
            == 0
        )
        assert (
            await db.scalar(select(func.count()).select_from(GenerationRun))
            == 0
        )
        assert (
            await db.scalar(select(func.count()).select_from(ApiUsageLog)) == 0
        )

"""History permissions, CSRF and exact UTC expiry through HTTP."""

from datetime import datetime, timedelta
from uuid import uuid4

import httpx
from starlette_csrf import CSRFMiddleware
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.config import config
from src.models import ComparisonRun

__all__ = ["client", "comparison", "scenario_payload", "student"]


async def test_history_delete_and_copy_require_owner_admin_and_delete_csrf(
    data, client, student, comparison
):
    from src.main import app

    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    copy_path = (
        f"/admin/scenarios/new?comparison_id={accepted['comparison_run_id']}"
    )
    client.cookies.clear()
    assert (await client.delete(path)).status_code == 401
    assert (await client.get("/admin/comparisons")).status_code == 401
    login(client, data.owner)
    assert (await client.delete(path)).status_code == 403
    assert (await client.get(copy_path)).status_code == 403
    data.other.role = "admin"
    await data.db.commit()
    login(client, data.other)
    for response in (
        await client.get(path),
        await client.delete(path),
        await client.get(copy_path),
    ):
        assert response.status_code == 404
        assert "PRIVATE" not in response.text
    protected = CSRFMiddleware(
        app, secret=config.SESSION_SECRET, header_name="x-csrf-token"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as secured:
        login(secured, data.admin)
        await secured.get(path)
        assert (await secured.delete(path)).status_code == 403
        secured.headers["x-csrf-token"] = secured.cookies["csrftoken"]
        assert (await secured.delete(path)).status_code == 204


async def test_exact_expiry_blocks_list_read_copy_retry_stop_delete_and_child(
    data, client, student, comparison, monkeypatch
):
    from src.services import comparison_history, comparison_state

    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    cutoff = datetime(2030, 1, 31, 12)
    async with data.factory() as db:
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.expires_at = cutoff
        record.created_at = cutoff - timedelta(days=30)
        await db.commit()
    clock = cutoff - timedelta(microseconds=1)
    monkeypatch.setattr(comparison_state, "utcnow", lambda: clock)
    monkeypatch.setattr(comparison_history, "utcnow", lambda: clock)
    assert (await client.get(path)).status_code == 200
    assert len((await client.get("/admin/comparisons")).json()["items"]) == 1
    clock = cutoff
    assert (await client.get("/admin/comparisons")).json()["items"] == []
    assert (await client.get(path)).status_code == 404
    assert (await client.delete(path)).status_code == 404
    assert (
        await client.get(
            "/admin/scenarios/new",
            params={"comparison_id": accepted["comparison_run_id"]},
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{path}/panels/1/retry",
            json={
                "request_id": str(uuid4()),
                "generation_id": accepted["run_id"],
            },
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{path}/panels/1/stop", json={"generation_id": accepted["run_id"]}
        )
    ).status_code == 404
    assert (
        await client.post(
            "/admin/comparisons",
            json={
                **comparison,
                "request_id": str(uuid4()),
                "parent_run_id": accepted["comparison_run_id"],
            },
        )
    ).status_code == 404
    student.responses.create.assert_awaited_once()

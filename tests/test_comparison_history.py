"""Owner history and deletion through HTTP, real SQLite and provider mocks."""

from datetime import timedelta
from uuid import uuid4

import pytest

from sqlalchemy import select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ComparisonRun

__all__ = ["client", "comparison", "scenario_payload", "student"]


async def test_history_is_owner_only_newest_first_twenty_without_input(
    data, client, student, comparison
):
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(response)[0][1]
    async with data.factory() as db:
        original = await db.get(ComparisonRun, accepted["comparison_run_id"])
        for index in range(24):
            values = {
                column.name: getattr(original, column.name)
                for column in ComparisonRun.__table__.columns
            }
            values.update(
                id=str(uuid4()),
                request_id=str(uuid4()),
                created_at=original.created_at + timedelta(seconds=index + 1),
            )
            db.add(ComparisonRun(**values))
        data.other.role = "admin"
        await data.db.commit()
        await db.commit()
    response = await client.get("/admin/comparisons")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    page = response.json()
    assert len(page["items"]) == 20 and page["next_cursor"]
    assert page["items"] == sorted(
        page["items"],
        key=lambda item: (item["created_at"], item["comparison_run_id"]),
        reverse=True,
    )
    assert all(
        "canonical_context" not in item and "source_snapshot" not in item
        for item in page["items"]
    )
    assert (
        "PRIVATE ANALYSIS" not in response.text
        and "Current question" not in response.text
    )
    next_page = (
        await client.get(
            "/admin/comparisons", params={"cursor": page["next_cursor"]}
        )
    ).json()
    assert len(next_page["items"]) == 5 and next_page["next_cursor"] is None
    assert (
        next_page["items"][-1]["comparison_run_id"]
        == accepted["comparison_run_id"]
    )
    login(client, data.other)
    assert (await client.get("/admin/comparisons")).json()["items"] == []
    login(client, data.owner)
    assert (await client.get("/admin/comparisons")).status_code == 403
    login(client, data.admin)
    assert (
        await client.get("/admin/comparisons", params={"cursor": "bad"})
    ).status_code == 422
    async with data.factory() as db:
        assert len((await db.scalars(select(ComparisonRun))).all()) == 25


async def test_terminal_delete_removes_content_preserves_cost_and_classroom_data(
    data, client, student, comparison
):
    from src.models import ApiUsageLog, GenerationRun
    from src.services.export import CSVExporter

    login(client, data.admin)
    stats = (await client.get("/admin/stats")).json()
    exporter = CSVExporter()

    async def exported(db):
        return [
            await exporter.export_session(data.session.id, db),
            await exporter.export_session_admin(data.session.id, db),
            await exporter.export_multiple_sessions([data.session.id], db),
            await exporter.export_multiple_sessions_admin(
                [data.session.id], db
            ),
        ]

    before_csv = await exported(data.db)
    response = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(response)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    async with data.factory() as db:
        usage = (await db.scalars(select(ApiUsageLog))).one()
        before = {
            column.name: getattr(usage, column.name)
            for column in ApiUsageLog.__table__.columns
        }
    import re

    cost_before = re.search(
        r'<dd id="known-cost">(.*?)</dd>',
        (await client.get("/admin/api-usage")).text,
    )[1]
    response = await client.delete(path)
    assert response.status_code == 204, response.text
    assert (await client.get(path)).status_code == 404
    assert (await client.delete(path)).status_code == 404
    assert (await client.get("/admin/comparisons")).json()["items"] == []
    async with data.factory() as db:
        assert (
            await db.get(ComparisonRun, accepted["comparison_run_id"]) is None
        )
        assert await db.get(GenerationRun, accepted["run_id"]) is None
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert {
            column.name: getattr(usage, column.name)
            for column in ApiUsageLog.__table__.columns
        } == {**before, "run_id": None}
        assert usage.usage_scope == "comparison" and usage.session_id is None
        assert await exported(db) == before_csv
    assert (await client.get("/admin/stats")).json() == stats
    usage_page = await client.get("/admin/api-usage")
    assert (
        re.search(r'<dd id="known-cost">(.*?)</dd>', usage_page.text)[1]
        == cost_before
    )
    assert "관리자 비교 (comparison)" in usage_page.text
    assert "Current question" not in usage_page.text


async def test_deleted_original_opens_fixed_new_draft_without_saving_or_extending_ttl(
    data, client, student, comparison
):
    import json
    import re

    from src.models import Scenario
    from src.services.comparison_state import utcnow

    login(client, data.admin)
    comparison["source_snapshot"].update(
        scenario_id=data.scenario.id,
        source_version=data.scenario.config_version,
        title="<img src=x onerror=alert(1)>",
    )
    response = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(response)[0][1]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    before = (await client.get(path)).json()
    data.scenario.deleted_at = utcnow()
    await data.db.commit()
    detail = (await client.get(path)).json()
    assert detail["source_available"] is False
    response = await client.get(
        "/admin/scenarios/new",
        params={"from_comparison": accepted["comparison_run_id"]},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "비교의 고정 조건을 새 초안에 열었습니다" in response.text
    editor = json.loads(
        re.search(
            r'<script id="scenario-editor-data" type="application/json">(.*?)</script>',
            response.text,
            re.S,
        )[1]
    )
    assert editor["id"] is None and editor["status"] == "draft"
    assert editor["config"] == before["source_snapshot"]["config"]
    assert editor["title"] == "<img src=x onerror=alert(1)>"
    assert "<img src=x onerror=alert(1)>" not in response.text
    assert editor["comparison_draft_id"] == accepted["comparison_run_id"]
    assert (await client.get(path)).json()["expires_at"] == before["expires_at"]
    async with data.factory() as db:
        assert len((await db.scalars(select(Scenario))).all()) == 1


async def test_active_delete_is_409_until_provider_and_ledger_finish(
    data, client, student, comparison
):
    import asyncio

    login(client, data.admin)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    task = asyncio.create_task(
        client.post("/admin/comparisons", json=comparison)
    )
    try:
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        record = (await client.get("/admin/comparisons")).json()["items"][0]
        path = f"/admin/comparisons/{record['comparison_run_id']}"
        assert (await client.delete(path)).status_code == 409
    finally:
        gate.set()
        await task
    assert (await client.delete(path)).status_code == 204


async def test_hard_scenario_and_parent_deletion_detach_links_keep_child_snapshot(
    data, client, student, comparison
):
    from sqlalchemy import delete

    from src.models import Scenario

    source = type(data.scenario)(
        title="Original", config_json=comparison["source_snapshot"]["config"]
    )
    data.db.add(source)
    await data.db.commit()
    comparison["source_snapshot"].update(
        scenario_id=source.id, source_version=source.config_version
    )
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    parent = frames(result)[0][1]["comparison_run_id"]
    result = await client.post(
        "/admin/comparisons",
        json={
            **comparison,
            "request_id": str(uuid4()),
            "parent_run_id": parent,
        },
    )
    child = frames(result)[0][1]["comparison_run_id"]
    before = (await client.get(f"/admin/comparisons/{child}")).json()
    assert before["source_available"] is True
    async with data.factory() as db:
        await db.execute(delete(Scenario).where(Scenario.id == source.id))
        await db.commit()
    assert (
        await client.delete(f"/admin/comparisons/{parent}")
    ).status_code == 204
    after = (await client.get(f"/admin/comparisons/{child}")).json()
    assert after["parent_run_id"] is None and after["source_available"] is False
    for key in (
        "source_snapshot",
        "canonical_context",
        "context_hash",
        "expires_at",
        "panels",
    ):
        assert after[key] == before[key]
    async with data.factory() as db:
        assert (await db.get(ComparisonRun, child)).scenario_id is None


@pytest.mark.parametrize("saved", [False, True])
async def test_history_list_distinguishes_new_draft_from_unsaved_scenario_edit(
    data, client, student, comparison, saved
):
    if saved:
        comparison["source_snapshot"].update(
            scenario_id=data.scenario.id,
            source_version=data.scenario.config_version,
        )
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 200
    item = (await client.get("/admin/comparisons")).json()["items"][0]
    assert item["source_origin"] == (
        "saved_scenario_edit" if saved else "new_draft"
    )
    assert "canonical_context" not in item and "current_message" not in item
    assert "source_snapshot" not in item

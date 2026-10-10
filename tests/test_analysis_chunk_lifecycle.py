"""Chunk runs never replace accepted reports after cancellation or failed adoption."""

import asyncio
import json

import httpx2
import pytest
from sqlalchemy import select, text
from test_analysis_chunks import chunk_reply, confirm
from test_analysis_invocations import USAGE, analysis_transport
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_scenario_api import login
from test_student_probe import response_body

from src.models import ApiUsageLog, SessionSummary
from src.services.analysis_runs import active_analyses
from src.services.generation_lifecycle import interrupt_orphans

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize(
    "original,outcome",
    [
        ("ok", "degraded"),
        ("legacy", "degraded"),
        ("degraded", "degraded"),
        ("degraded", "failed"),
        ("ok", "storage"),
        ("ok", "cancel"),
        ("degraded", "restart"),
        ("degraded", "permission"),
    ],
)
async def test_chunk_adoption_and_interruptions_preserve_accepted_report(
    data, api, monkeypatch, original, outcome
):
    await long_session(data, monkeypatch)
    initial, entered, release = True, asyncio.Event(), asyncio.Event()
    later_calls = 0

    async def upstream(request, body):
        nonlocal later_calls
        value = chunk_reply(body)
        if initial:
            if original == "degraded" and value.get("message_classifications"):
                value["message_classifications"][0].update(
                    disposition="unclassified", rubric_id=None
                )
        else:
            later_calls += 1
            if outcome == "degraded" and later_calls == 2:
                value["message_classifications"][0][
                    "quote"
                ] = "PRIVATE BAD QUOTE"
            elif outcome == "failed" and later_calls == 1:
                return httpx2.Response(
                    200, json=response_body("{PRIVATE JSON", USAGE)
                )
            elif outcome in {"cancel", "restart"} and later_calls == 2:
                entered.set()
                await release.wait()
            elif outcome == "permission" and later_calls == 1:
                data.owner.group_id = None
                await data.db.commit()
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    if original == "legacy":
        data.db.add(
            SessionSummary(
                session_id=data.session.id,
                distribution_json='{"A":1}',
                feedback="Original legacy",
            )
        )
        await data.db.commit()
    else:
        first, _, _ = await confirm(api, data.session.id)
        original_result = await terminal(api, first["actions"]["status"])
        assert original_result["accepted_report"]["status"] == original
    initial = False
    admin = original in {"ok", "legacy"}
    if admin:
        login(api, data.admin)
        await api.get("/health")
    if outcome == "storage":
        async with data.factory() as db:
            await db.execute(
                text(
                    "CREATE TRIGGER reject_report BEFORE INSERT ON session_feedback_report BEGIN SELECT RAISE(ABORT,'PRIVATE STORAGE ERROR'); END"
                )
            )
            await db.commit()
    accepted, _, body = await confirm(api, data.session.id, admin=admin)
    previous = accepted["accepted_report"]
    try:
        if outcome in {"cancel", "restart"}:
            await asyncio.wait_for(entered.wait(), 5)
            if outcome == "cancel":
                cancelled = await api.post(
                    accepted["actions"]["cancel"],
                    headers={"x-csrf-token": api.cookies["csrftoken"]},
                )
                assert cancelled.status_code == 202
            else:
                await interrupt_orphans(data.factory)
    finally:
        release.set()
    result = await terminal(api, accepted["actions"]["status"])
    expected = {
        "degraded": "degraded",
        "failed": "failed",
        "storage": "failed",
        "cancel": "cancelled",
        "restart": "interrupted",
        "permission": "failed",
    }[outcome]
    assert result["latest_run"]["status"] == expected
    replaced = original == outcome == "degraded"
    assert result["latest_run"]["adopted"] is replaced
    assert result["latest_run"]["preserved"] is not replaced
    if replaced:
        assert result["accepted_report"]["run_id"] == accepted["run_id"]
    else:
        assert result["accepted_report"] == previous
        assert result["distribution"] == accepted["distribution"]
        assert result["questions"] == accepted["questions"]
    assert (
        later_calls
        == {
            "degraded": 2,
            "failed": 1,
            "storage": 3,
            "cancel": 2,
            "restart": 2,
            "permission": 1,
        }[outcome]
    )
    if outcome == "storage":
        assert result["latest_run"]["error_code"] == "storage_failed"
    assert "PRIVATE" not in json.dumps(result)
    url = (
        f"/admin/sessions/{data.session.id}/analyze_regenerate"
        if admin
        else f"/sessions/{data.session.id}/analyze"
    )
    count = len(calls)
    replay = await api.post(
        url, json=body, headers={"x-csrf-token": api.cookies["csrftoken"]}
    )
    assert replay.json()["run_id"] == accepted["run_id"] and len(calls) == count
    await asyncio.gather(*list(active_analyses.values()))
    assert all(c.is_closed() for c in clients)
    if outcome in {"cancel", "restart"}:
        async with data.factory() as db:
            ledger = list(
                await db.scalars(
                    select(ApiUsageLog)
                    .where(ApiUsageLog.run_id == accepted["run_id"])
                    .order_by(ApiUsageLog.id)
                )
            )
        assert ledger[0].status == "completed"
        assert ledger[1].status == (
            "cancelled" if outcome == "cancel" else "completed"
        )


async def test_regeneration_role_is_rechecked_before_the_next_chunk_even_for_owner(
    data, api, monkeypatch
):
    await long_session(data, monkeypatch)
    data.session.teacher_id = data.admin.id
    data.admin.group_id = data.owner.group_id
    await data.db.commit()
    login(api, data.admin)
    await api.get("/health")

    async def upstream(request, body):
        if len(calls) == 1:
            data.admin.role = "teacher"
            await data.db.commit()
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted, _, _ = await confirm(api, data.session.id, admin=True)
    result = await terminal(
        api, accepted["actions"]["status"].removeprefix("/admin")
    )
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "configuration_unavailable"
    assert result["accepted_report"] is None
    assert len(calls) == 1

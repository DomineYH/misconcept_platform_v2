"""Durable analysis lifecycle through HTTP, SQLite and mocked provider HTTP."""

import asyncio
import json
from uuid import uuid4

import httpx2
import pytest
from test_analysis_invocations import (
    USAGE,
    analysis_transport,
    prepare_analysis,
    result_for,
)
from test_analysis_invocations import api as analysis_api
from test_analysis_invocations import connection_api as provider_api
from test_student_probe import response_body

api = analysis_api
connection_api = provider_api

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_reservation_replays_without_call_and_rejects_competing_request(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    try:
        accepted = await asyncio.wait_for(
            api.post(url, json=body, headers=headers), 2
        )
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        await asyncio.wait_for(entered.wait(), 5)
        replay = await api.post(url, json=body, headers=headers)
        assert replay.json()["run_id"] == run_id
        conflict = await api.post(
            url, json={"request_id": str(uuid4())}, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["run_id"] == run_id
        assert len(calls) == 1
    finally:
        release.set()
    for _ in range(100):
        result = await api.get(accepted.json()["actions"]["status"])
        if result.json()["latest_run"]["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert result.json()["latest_run"]["status"] == "ok"
    assert result.json()["accepted_report"]["run_id"] == run_id


async def terminal(api, url):
    for _ in range(250):
        result = await api.get(url)
        assert result.status_code == 200, result.text
        if result.json()["latest_run"]["status"] != "running":
            return result.json()
        await asyncio.sleep(0.02)
    pytest.fail("Analysis did not finalize")


@pytest.mark.parametrize("action", ["cancel", "revoke", "restart"])
async def test_unadoptable_run_keeps_no_report_and_never_reexecutes(
    data, api, monkeypatch, action
):
    from src.services.generation_lifecycle import interrupt_orphans

    await prepare_analysis(data, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    accepted = await api.post(url, json=body, headers=headers)
    assert accepted.status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    state_url = accepted.json()["actions"]["status"]
    try:
        if action == "cancel":
            cancelled = await api.post(
                accepted.json()["actions"]["cancel"], headers=headers
            )
            assert cancelled.status_code == 202
        elif action == "revoke":
            data.owner.group_id = None
            await data.db.commit()
        else:
            await interrupt_orphans(data.factory)
    finally:
        release.set()
    result = await terminal(api, state_url)
    assert (
        result["latest_run"]["status"]
        == {
            "cancel": "cancelled",
            "revoke": "failed",
            "restart": "interrupted",
        }[action]
    )
    assert result["accepted_report"] is None
    replay = await api.post(url, json=body, headers=headers)
    assert replay.json()["run_id"] == accepted.json()["run_id"]
    assert len(calls) == 1
    from src.services.analysis_runs import active_analyses

    await asyncio.gather(*list(active_analyses.values()))
    assert all(c.is_closed() for c in clients)


async def test_changed_input_replay_conflicts_and_old_run_is_superseded(
    data, api, monkeypatch
):
    from test_scenario_api import login

    from src.models import Message

    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        value["brief_feedback"] = [f"Report {len(calls)}"]
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    first = await api.post(url, json=body, headers=headers)
    old = await terminal(api, first.json()["actions"]["status"])
    assert old["accepted_report"]["brief_feedback"] == ["Report 1"]
    login(api, data.admin)
    await api.get("/health")
    newer = await api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        json={"request_id": str(uuid4())},
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert newer.status_code == 202
    await terminal(api, newer.json()["actions"]["status"])
    login(api, data.owner)
    past = await api.get(first.json()["actions"]["status"])
    assert past.json()["latest_run"]["superseded"] is True
    assert past.json()["accepted_report"] is None
    assert "Report 2" not in past.text
    await api.get("/health")
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    replay = await api.post(url, json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["latest_run"]["superseded"] is True
    assert "Report 2" not in replay.text
    data.db.add(
        Message(
            session_id=data.session.id, role="student", content="New answer"
        )
    )
    await data.db.commit()
    mismatch = await api.post(url, json=body, headers=headers)
    assert mismatch.status_code == 409 and len(calls) == 2
    assert len(calls) == 2


@pytest.mark.parametrize("preserve", [False, True])
async def test_storage_failure_never_adopts_or_reports_completion(
    data, api, monkeypatch, preserve
):
    from sqlalchemy import text
    from test_scenario_api import login

    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    if preserve:
        first = await api.post(
            f"/sessions/{data.session.id}/analyze",
            json={"request_id": str(uuid4())},
            headers=headers,
        )
        original = (await terminal(api, first.json()["actions"]["status"]))[
            "accepted_report"
        ]
    async with data.factory() as db:
        await db.execute(
            text(
                "CREATE TRIGGER reject_report BEFORE INSERT ON session_feedback_report BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END"
            )
        )
        await db.commit()
    login(api, data.admin)
    await api.get("/health")
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    accepted = await api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        json={"request_id": str(uuid4())},
        headers=headers,
    )
    assert accepted.status_code == 202
    result = await terminal(api, accepted.json()["actions"]["status"])
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "storage_failed"
    assert result["latest_run"]["adopted"] is False
    assert result["accepted_report"] == (original if preserve else None)
    assert len(calls) == 1 + preserve


async def test_run_deadline_bounds_provider_wait_and_does_not_restart(
    data, api, monkeypatch
):
    from src.services import analysis_runs

    await prepare_analysis(data, monkeypatch)
    entered = asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await asyncio.Event().wait()

    clients, calls = analysis_transport(monkeypatch, upstream)
    monkeypatch.setattr(analysis_runs, "RUN_SECONDS", 3)
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    accepted = await api.post(
        f"/sessions/{data.session.id}/analyze", json=body, headers=headers
    )
    assert accepted.status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    result = await terminal(api, accepted.json()["actions"]["status"])
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "timeout_total"
    assert result["accepted_report"] is None
    replay = await api.post(
        f"/sessions/{data.session.id}/analyze", json=body, headers=headers
    )
    assert replay.json()["run_id"] == accepted.json()["run_id"]
    assert len(calls) == 1 and all(client.is_closed() for client in clients)


@pytest.mark.parametrize(
    "actor,with_body", [("teacher", True), ("teacher", False), ("admin", True)]
)
async def test_end_commits_then_reserves_and_replays_same_run(
    data, api, monkeypatch, actor, with_body
):
    import httpx
    from test_scenario_api import login

    from src.main import app
    from src.models import Session

    await prepare_analysis(data, monkeypatch)
    data.session.ended_at = None
    await data.db.commit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        async with data.factory() as db:
            assert (await db.get(Session, data.session.id)).ended_at is not None
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    login(api, data.admin if actor == "admin" else data.owner)
    await api.get("/health")
    prefix = "/admin" if actor == "admin" else ""
    analyze = "analyze_regenerate" if actor == "admin" else "analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies=api.cookies,
        ) as browser:
            accepted = await browser.post(
                f"{prefix}/sessions/{data.session.id}/end",
                headers=headers,
                **({"json": body} if with_body else {}),
            )
        # Closing the end-request client cannot cancel the reserved run.
        assert accepted.status_code == 202
        await asyncio.wait_for(entered.wait(), 5)
        replay = await api.post(
            f"{prefix}/sessions/{data.session.id}/end",
            headers=headers,
            **({"json": body} if with_body else {}),
        )
        assert replay.json()["run_id"] == accepted.json()["run_id"]
        competing = await api.post(
            f"{prefix}/sessions/{data.session.id}/{analyze}",
            json={"request_id": str(uuid4())},
            headers=headers,
        )
        assert competing.status_code == 409
    finally:
        release.set()
    assert (await terminal(api, accepted.json()["actions"]["status"]))[
        "latest_run"
    ]["status"] == "ok"
    assert len(calls) == 1


async def test_regeneration_cannot_adopt_after_admin_role_revoked_even_for_owner(
    data, api, monkeypatch
):
    from test_scenario_api import login

    await prepare_analysis(data, monkeypatch)
    data.session.teacher_id = data.admin.id
    await data.db.commit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    login(api, data.admin)
    await api.get("/health")
    accepted = await api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        json={"request_id": str(uuid4())},
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert accepted.status_code == 202
    try:
        await asyncio.wait_for(entered.wait(), 5)
        data.admin.role = "teacher"
        await data.db.commit()
    finally:
        release.set()
    result = await terminal(
        api, accepted.json()["actions"]["status"].removeprefix("/admin")
    )
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "configuration_unavailable"
    assert result["accepted_report"] is None
    assert len(calls) == 1


async def test_run_lookup_and_cancellation_keep_owner_and_admin_boundaries(
    data, api, monkeypatch
):
    from test_scenario_api import login

    from src.services.analysis_runs import active_analyses

    await prepare_analysis(data, monkeypatch)
    entered = asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await asyncio.Event().wait()

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted = await api.post(
        f"/sessions/{data.session.id}/analyze",
        json={"request_id": str(uuid4())},
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert accepted.status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    task = active_analyses[accepted.json()["run_id"]]
    status_url, cancel_url = (
        accepted.json()["actions"]["status"],
        accepted.json()["actions"]["cancel"],
    )
    login(api, data.other)
    await api.get("/health")
    assert (await api.get(status_url)).status_code == 403
    assert (
        await api.post(
            cancel_url, headers={"x-csrf-token": api.cookies["csrftoken"]}
        )
    ).status_code == 403
    assert (await api.get("/admin" + status_url)).status_code == 403
    login(api, data.admin)
    await api.get("/health")
    assert (await api.get("/admin" + status_url)).json()["latest_run"][
        "status"
    ] == "running"
    cancelled = await api.post(
        "/admin" + cancel_url,
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert (
        cancelled.status_code == 202
        and cancelled.json()["latest_run"]["status"] == "cancelled"
    )
    await asyncio.wait_for(task, 5)
    assert len(calls) == 1


async def test_admin_end_html_exposes_running_analysis_before_report_exists(
    data, api, monkeypatch
):
    from test_scenario_api import login

    from src.services.analysis_runs import active_analyses

    await prepare_analysis(data, monkeypatch)
    data.session.ended_at = None
    await data.db.commit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    login(api, data.admin)
    await api.get("/health")
    try:
        response = await api.post(
            f"/admin/sessions/{data.session.id}/end",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert response.status_code == 202
        await asyncio.wait_for(entered.wait(), 5)
        assert "view-analysis-btn" in response.text
        listing = await api.get("/admin/sessions-page")
        assert "view-analysis-btn" in listing.text
        modal = await api.get(
            f"/admin/sessions/{data.session.id}/analysis_modal"
        )
        assert modal.status_code == 200
        assert (
            f'data-result-url="/admin/sessions/{data.session.id}/analysis"'
            in modal.text
        )
        result = (
            await api.get(f"/admin/sessions/{data.session.id}/analysis")
        ).json()
        assert result["latest_run"]["status"] == "running"
        assert result["accepted_report"] is None and result["actions"]["cancel"]
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(*list(active_analyses.values()))


async def test_unexpected_execution_failure_is_not_a_storage_failure(
    data, api, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.services import analysis_pipeline

    await prepare_analysis(data, monkeypatch)
    monkeypatch.setattr(
        analysis_pipeline,
        "run_llm_pipeline",
        AsyncMock(
            side_effect=RuntimeError("PRIVATE unexpected execution failure")
        ),
    )
    body = {"request_id": str(uuid4())}
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    accepted = await api.post(
        f"/sessions/{data.session.id}/analyze", json=body, headers=headers
    )
    assert accepted.status_code == 202
    result = await terminal(api, accepted.json()["actions"]["status"])
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "analysis_failed"
    assert result["accepted_report"] is None
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("collision", ["request", "running"])
async def test_unique_reservation_collision_recovers_replay_or_busy(
    data, api, monkeypatch, collision
):
    from sqlalchemy import event

    await prepare_analysis(data, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, body):
        entered.set()
        await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    body = {"request_id": str(uuid4())}
    accepted = await api.post(url, json=body, headers=headers)
    assert accepted.status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    if collision == "request":
        await api.post(accepted.json()["actions"]["cancel"], headers=headers)
        await terminal(api, accepted.json()["actions"]["status"])
    hidden, inserts = set(), []

    def stale_read(conn, cursor, statement, parameters, context, executemany):
        # Simulate a stale pre-insert read; the real SQLite indexes reject insertion.
        if statement.startswith("INSERT INTO generation_run"):
            inserts.append(statement)
        for column in ("request_id", "status"):
            if (
                f"generation_run.{column} = ?" in statement
                and column not in hidden
                and (column == "status" or collision == "request")
            ):
                hidden.add(column)
                statement = statement.replace(
                    f"generation_run.{column} = ?",
                    f"generation_run.{column} = ? AND 0",
                )
                break
        return statement, parameters

    event.listen(
        data.engine.sync_engine,
        "before_cursor_execute",
        stale_read,
        retval=True,
    )
    try:
        competing = await api.post(
            url,
            json=(
                body if collision == "request" else {"request_id": str(uuid4())}
            ),
            headers=headers,
        )
        if collision == "request":
            assert competing.status_code == 200
            assert competing.json()["run_id"] == accepted.json()["run_id"]
        else:
            assert competing.status_code == 409
            assert competing.json()["detail"] == {
                "code": "analysis_busy",
                "run_id": accepted.json()["run_id"],
            }
        assert len(inserts) == 1
        assert len(calls) == 1
    finally:
        event.remove(
            data.engine.sync_engine, "before_cursor_execute", stale_read
        )
        release.set()
        await terminal(api, accepted.json()["actions"]["status"])

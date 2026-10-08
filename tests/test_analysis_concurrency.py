"""Runtime analysis limits, cancellation, deadlines and durable result readers."""

import asyncio
import json

import httpx2
import pytest
from sqlalchemy import select, text
from test_analysis_invocations import (
    USAGE,
    analysis_transport,
    prepare_analysis,
    result_for,
)
from test_analysis_invocations import (
    api as analysis_api,
)
from test_analysis_invocations import (
    connection_api as provider_api,
)
from test_scenario_api import login
from test_student_probe import response_body

from src.models import ApiUsageLog, AppSetting, Message, Session, SessionSummary
from src.services.analysis_results import save_analysis

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize("limiter", ["total", "openai"])
async def test_parallel_analysis_respects_limits_and_allows_other_session_write(
    data, api, monkeypatch, limiter
):
    await prepare_analysis(data, monkeypatch)
    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = {**setting.limits_json, limiter: 2, "admin": 1}
    for _ in range(3):
        data.db.add(
            Message(session_id=data.session.id, role="teacher", content="Why?")
        )
    other_session = Session(
        scenario_id=data.scenario.id, teacher_id=data.other.id
    )
    data.db.add(other_session)
    await data.db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def upstream(request, body):
        nonlocal active, peak
        if body["text"]["format"]["name"] == "RuntimeClassification":
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
            finally:
                active -= 1
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    task = asyncio.create_task(
        api.post(
            f"/sessions/{data.session.id}/analyze",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with data.factory() as db:
            assert await db.scalar(text("PRAGMA journal_mode")) == "wal"
            await db.execute(text("PRAGMA busy_timeout=100"))
            db.add(
                Message(
                    session_id=other_session.id,
                    role="teacher",
                    content="Concurrent write",
                )
            )
            await db.commit()
        # Keep both provider requests blocked until other admissions finish.
        await asyncio.sleep(0.1)
    finally:
        release.set()
    response = await asyncio.wait_for(task, 5)
    assert response.status_code == 200 and peak == 2
    assert len(calls) == 4 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (await db.scalars(select(ApiUsageLog))).all()
        assert len(rows) == 4  # Refused slots are not provider attempts.
        assert (
            await db.scalar(
                select(Message.content).where(
                    Message.session_id == other_session.id
                )
            )
            == "Concurrent write"
        )


async def test_retry_success_reuses_result_and_keeps_attempt_times(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    classification_calls = 0

    async def upstream(request, body):
        nonlocal classification_calls
        if body["text"]["format"]["name"] == "RuntimeClassification":
            classification_calls += 1
            if classification_calls == 1:
                return httpx2.Response(
                    503,
                    json={"error": {"code": "server_error"}},
                    headers={"Retry-After": "0.05"},
                )
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    path = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    first = await api.post(path, headers=headers)
    assert first.json()["distribution"] == {"A": 1, "B": 0}
    assert (await api.post(path, headers=headers)).json() == first.json()
    assert len(calls) == 4 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.operation == "classification")
                .order_by(ApiUsageLog.id)
            )
        ).all()
    assert [(r.attempt_no, r.status, r.retry_wait_ms) for r in rows] == [
        (1, "failed", None),
        (2, "completed", 50),
    ]
    assert rows[0].invocation_id == rows[1].invocation_id
    assert (rows[1].finished_at - rows[0].started_at).total_seconds() >= 0.05
    assert (
        await api.get(f"/sessions/{data.session.id}/export.csv")
    ).status_code == 200


async def test_analysis_does_not_retry_when_backoff_exceeds_remaining_deadline(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {**setting.timeouts_json, "analysis_total": 1}
    await data.db.commit()

    async def upstream(request, body):
        if body["text"]["format"]["name"] == "RuntimeClassification":
            return httpx2.Response(
                503,
                json={"error": {"code": "server_error"}},
                headers={"Retry-After": "2"},
            )
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    assert len(calls) == 3 and all(c.is_closed() for c in clients)


async def test_cancelled_analysis_finalizes_attempt_without_synthesis_or_retry(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    entered = asyncio.Event()

    async def upstream(request, body):
        if body["text"]["format"]["name"] == "RuntimeClassification":
            entered.set()
            await asyncio.Event().wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    task = asyncio.create_task(
        api.post(
            f"/sessions/{data.session.id}/analyze",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
        assert [(r.operation, r.status) for r in rows] == [
            ("greeting", "completed"),
            ("classification", "cancelled"),
        ]
        assert (await db.scalars(select(SessionSummary))).all() == []


async def test_failed_regeneration_preserves_report_but_keeps_new_attempt_ledger(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    await save_analysis(
        data.session.id,
        (
            {"A": 1},
            [],
            {"brief_feedback": ["original"]},
            "ok",
            "test",
            "hash",
            [],
        ),
        data.db,
    )
    login(api, data.admin)
    await api.get("/admin/ai")

    async def upstream(request, body):
        return httpx2.Response(200, json=response_body("not json", USAGE))

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    assert response.json()["feedback"] == "original"
    assert (
        response.json()["regeneration_status"] == "synthesis_failed_preserved"
    )
    assert len(calls) == 3 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (await db.scalars(select(ApiUsageLog))).all()
    assert len(rows) == 3 and all(r.error_code == "invalid_json" for r in rows)


async def test_cancel_during_analysis_backoff_does_not_create_another_attempt(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        if body["text"]["format"]["name"] == "RuntimeClassification":
            return httpx2.Response(
                503,
                json={"error": {"code": "server_error"}},
                headers={"Retry-After": "30"},
            )
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    task = asyncio.create_task(
        api.post(
            f"/sessions/{data.session.id}/analyze",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    )
    try:
        async with asyncio.timeout(5):
            while True:
                async with data.factory() as db:
                    status = await db.scalar(
                        select(ApiUsageLog.status).where(
                            ApiUsageLog.operation == "classification"
                        )
                    )
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (await db.scalars(select(ApiUsageLog))).all()
        assert len(rows) == 2 and all(r.attempt_no == 1 for r in rows)
        assert (await db.scalars(select(SessionSummary))).all() == []


async def test_analysis_total_timeout_is_finalized_without_retry(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)
    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {**setting.timeouts_json, "analysis_total": 1}
    await data.db.commit()

    async def upstream(request, body):
        if body["text"]["format"]["name"] == "RuntimeClassification":
            await asyncio.Event().wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await asyncio.wait_for(
        api.post(
            f"/sessions/{data.session.id}/analyze",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        ),
        5,
    )
    assert response.status_code == 200
    assert len(calls) == 3 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (
            await db.scalars(
                select(ApiUsageLog).where(
                    ApiUsageLog.operation == "classification"
                )
            )
        ).all()
    assert len(rows) == 1 and rows[0].status == "timed_out"
    assert (
        rows[0].error_code == "timeout_total"
        and rows[0].finished_at is not None
    )

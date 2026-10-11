from unittest.mock import AsyncMock

import pytest
from analysis_fixtures import install_analysis_snapshot
from analysis_test_helpers import call_analysis_route
from sqlalchemy import select
from test_regressions import request

from src.api.routes import session_analysis as routes
from src.models import SessionFeedbackReport, SessionSummary
from src.services import analysis_pipeline as pipeline


@pytest.fixture(autouse=True)
async def native_configuration(data, monkeypatch):
    await install_analysis_snapshot(data, monkeypatch)


async def test_failed_analysis_retries_then_reuses_success(data, monkeypatch):
    sid = data.session.id
    monkeypatch.setattr(
        pipeline,
        "run_llm_pipeline",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    failed = await call_analysis_route(
        routes.analyze_session_endpoint, request(), sid, data.owner, data.db
    )
    assert failed["latest_run"]["status"] == "failed"
    assert failed["accepted_report"] is None
    fake = AsyncMock(
        return_value=(
            {"A": 1},
            [],
            {"brief_feedback": ["success"]},
            "ok",
            "test",
            "hash",
            [],
        )
    )
    monkeypatch.setattr(pipeline, "run_llm_pipeline", fake)
    result = await call_analysis_route(
        routes.analyze_session_endpoint, request(), sid, data.owner, data.db
    )
    assert fake.await_count == 1
    assert result["feedback"] == "success"
    assert result["feedback_status"] == "ok"
    assert result["retryable"] is False
    assert (
        await call_analysis_route(
            routes.analyze_session_endpoint, request(), sid, data.owner, data.db
        )
        == result
    )
    assert fake.await_count == 1
    assert len((await data.db.scalars(select(SessionSummary))).all()) == 1
    assert (
        len((await data.db.scalars(select(SessionFeedbackReport))).all()) == 1
    )


async def test_legacy_and_degraded_policy(data, monkeypatch):
    from src.services.analysis_results import load_summary

    sid = data.session.id
    fake = AsyncMock(
        return_value=(
            {},
            [],
            {"brief_feedback": ["recovered"]},
            "ok",
            "test",
            "hash",
            [],
        )
    )
    monkeypatch.setattr(pipeline, "run_llm_pipeline", fake)
    summary = SessionSummary(
        session_id=sid, distribution_json="{}", feedback="old success"
    )
    data.db.add(summary)
    await data.db.commit()
    response = await call_analysis_route(
        routes.analyze_session_endpoint, request(), sid, data.owner, data.db
    )
    assert response["feedback_status"] == "legacy"
    assert fake.await_count == 0
    summary.feedback = pipeline.FALLBACK_FEEDBACK
    await data.db.commit()
    response = await call_analysis_route(
        routes.analyze_session_endpoint, request(), sid, data.owner, data.db
    )
    assert response["feedback"] == "recovered"
    assert fake.await_count == 1
    _, report = await load_summary(sid, data.db)
    report.status = "degraded"
    await data.db.commit()
    response = await call_analysis_route(
        routes.analyze_session_endpoint, request(), sid, data.owner, data.db
    )
    assert response["feedback_status"] == "ok"
    assert response["latest_run"]["adopted"] is True
    assert fake.await_count == 2


async def test_concurrent_retries_and_late_failure_keep_success(
    data, monkeypatch
):
    import asyncio
    from uuid import uuid4

    from src.api.routes import admin_session_actions as actions
    from src.models import Message, QuestionAnalysis
    from src.services.analysis_runs import execute_analysis, reserve_analysis

    sid = data.session.id
    msg = Message(session_id=sid, role="teacher", content="Why?")
    data.db.add(msg)
    await data.db.commit()
    fake = AsyncMock(
        return_value=(
            {"A": 1},
            [QuestionAnalysis(message_id=msg.id, label="A")],
            {"brief_feedback": ["success"]},
            "ok",
            "test",
            "hash",
            [],
        )
    )
    monkeypatch.setattr(pipeline, "run_llm_pipeline", fake)
    request_id = str(uuid4())
    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                reserve_analysis(data.factory, sid, data.owner.id, request_id)
                for _ in range(2)
            ]
        ),
        timeout=5,
    )
    assert results[0][0]["run_id"] == results[1][0]["run_id"]
    executions = [
        execution for _, execution in results if execution is not None
    ]
    assert len(executions) == 1
    await execute_analysis(data.factory, executions[0])
    assert fake.await_count == 1
    monkeypatch.setattr(
        pipeline,
        "run_llm_pipeline",
        AsyncMock(side_effect=RuntimeError("late failure")),
    )
    result = await call_analysis_route(
        actions.regenerate_analysis, request(), sid, data.admin, data.db
    )
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["preserved"] is True
    assert result["feedback"] == "success"
    async with data.factory() as db:
        for model in (SessionSummary, SessionFeedbackReport, QuestionAnalysis):
            assert len((await db.scalars(select(model))).all()) == 1


async def test_admin_cannot_replace_good_result_with_failed_or_degraded(
    data, monkeypatch
):
    from src.api.routes import admin_session_actions as actions
    from src.services.analysis_results import save_analysis

    sid = data.session.id
    await save_analysis(
        sid,
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
    for status in ("failed", "degraded"):
        monkeypatch.setattr(
            pipeline,
            "run_llm_pipeline",
            AsyncMock(return_value=({}, [], {}, status, "test", "hash", [])),
        )
        result = await call_analysis_route(
            actions.regenerate_analysis, request(), sid, data.admin, data.db
        )
        assert result["feedback"] == "original"
        assert result["feedback_status"] == "ok"
        assert result["regeneration_status"].endswith("_preserved")

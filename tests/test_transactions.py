from unittest.mock import AsyncMock

import pytest
from analysis_fixtures import install_analysis_snapshot
from analysis_test_helpers import call_analysis_route
from sqlalchemy import select, text
from test_regressions import request

from src.api.routes import admin_session_actions as actions
from src.models import Session, SessionFeedbackReport, SessionSummary
from src.services import analysis_pipeline as pipeline


@pytest.fixture(autouse=True)
async def native_configuration(data, monkeypatch):
    await install_analysis_snapshot(data, monkeypatch)


async def test_admin_end_releases_writer_before_analysis(data, monkeypatch):
    sid = data.session.id
    data.session.ended_at = None
    await data.db.commit()
    writes = []

    async def analyze(*args, **kwargs):
        async with data.factory() as other:
            await other.execute(text("PRAGMA busy_timeout=100"))
            await other.execute(
                text("UPDATE user SET nickname='Concurrent' WHERE id=:id"),
                {"id": data.other.id},
            )
            await other.commit()
            ended = await other.get(Session, sid)
            assert ended.ended_at is not None
            writes.append(True)
        raise RuntimeError("LLM failure")

    monkeypatch.setattr(pipeline, "run_llm_pipeline", analyze)
    response = await actions.end_session(
        request(), sid, user=data.admin, db=data.db
    )
    assert response.status_code == 202
    import asyncio

    from src.services.analysis_runs import active_analyses

    await asyncio.gather(*list(active_analyses.values()))
    assert writes == [True]
    async with data.factory() as reader:
        assert (await reader.get(Session, sid)).ended_at is not None


async def test_analysis_save_failure_is_atomic(data, monkeypatch):
    fake = AsyncMock(
        return_value=({"A": 1}, [], {}, "invalid-status", "test", "hash", [])
    )
    monkeypatch.setattr(pipeline, "run_llm_pipeline", fake)
    with pytest.raises(Exception):
        await pipeline.analyze_session(
            data.session.id,
            data.session,
            data.db,
        )
    await data.db.rollback()
    async with data.factory() as reader:
        assert (await reader.scalars(select(SessionSummary))).all() == []
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []


async def test_regeneration_save_failure_preserves_old(data, monkeypatch):
    sid = data.session.id
    data.db.add(
        SessionSummary(
            session_id=sid, distribution_json="{}", feedback="original"
        )
    )
    await data.db.commit()
    monkeypatch.setattr(
        pipeline,
        "run_llm_pipeline",
        AsyncMock(
            return_value=({}, [], {}, "invalid-status", "test", "hash", [])
        ),
    )
    result = await call_analysis_route(
        actions.regenerate_analysis, request(), sid, data.admin, data.db
    )
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["adopted"] is False
    await data.db.rollback()
    async with data.factory() as reader:
        assert (
            await reader.scalars(select(SessionSummary))
        ).one().feedback == "original"
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []


async def test_admin_end_recovers_after_database_failure(data, monkeypatch):
    sid = data.session.id
    data.session.ended_at = None
    await data.db.commit()

    async def fail(*args, **kwargs):
        async with data.factory() as db:
            db.add(
                SessionFeedbackReport(
                    session_id=sid,
                    version=1,
                    model="test",
                    prompt_hash="hash",
                    status="invalid",
                    payload_json="{}",
                )
            )
            await db.flush()

    monkeypatch.setattr(pipeline, "run_llm_pipeline", fail)
    response = await actions.end_session(
        request(), sid, user=data.admin, db=data.db
    )
    assert response.status_code == 202
    import asyncio

    from src.services.analysis_runs import active_analyses

    await asyncio.gather(*list(active_analyses.values()))
    assert "완료" in response.body.decode()
    async with data.factory() as reader:
        assert (await reader.get(Session, sid)).ended_at is not None
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []

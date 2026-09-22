from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from src.api.routes import admin_session_actions as actions
from src.models import Session, SessionSummary, SessionFeedbackReport
from src.services import analysis_pipeline as pipeline
from test_regressions import request


async def test_admin_end_releases_writer_before_analysis(data, monkeypatch):
    sid = data.session.id
    data.session.ended_at = None
    await data.db.commit()
    writes = []
    async def analyze(*args):
        async with data.factory() as other:
            await other.execute(text('PRAGMA busy_timeout=100'))
            await other.execute(text("UPDATE user SET nickname='Concurrent' WHERE id=:id"), {"id": data.other.id})
            await other.commit()
            ended = await other.get(Session, sid)
            assert ended.ended_at is not None
            writes.append(True)
        raise RuntimeError('LLM failure')
    monkeypatch.setattr(actions, 'analyze_session', analyze)
    response = await actions.end_session(request(), sid, data.admin, data.db)
    assert response.status_code == 200
    assert writes == [True]
    async with data.factory() as reader:
        assert (await reader.get(Session, sid)).ended_at is not None


async def test_analysis_save_failure_is_atomic(data, monkeypatch):
    fake = AsyncMock(return_value=({'A': 1}, [], {}, 'invalid-status', 'test', 'hash', []))
    monkeypatch.setattr(pipeline, 'run_llm_pipeline', fake)
    with pytest.raises(Exception):
        await pipeline.analyze_session(data.session.id, data.session, data.scenario, data.framework, data.db)
    await data.db.rollback()
    async with data.factory() as reader:
        assert (await reader.scalars(select(SessionSummary))).all() == []
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []


async def test_regeneration_save_failure_preserves_old(data, monkeypatch):
    sid = data.session.id
    data.db.add(SessionSummary(session_id=sid, distribution_json='{}', feedback='original'))
    await data.db.commit()
    monkeypatch.setattr(actions, 'run_llm_pipeline', AsyncMock(return_value=({}, [], {}, 'invalid-status', 'test', 'hash', [])))
    with pytest.raises(Exception):
        await actions.regenerate_analysis(request(), sid, data.admin, data.db)
    await data.db.rollback()
    async with data.factory() as reader:
        assert (await reader.scalars(select(SessionSummary))).one().feedback == 'original'
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []


async def test_admin_end_recovers_after_database_failure(data, monkeypatch):
    sid = data.session.id
    data.session.ended_at = None
    await data.db.commit()
    async def fail(*args):
        data.db.add(SessionFeedbackReport(session_id=sid, version=1, model='test', prompt_hash='hash', status='invalid', payload_json='{}'))
        await data.db.flush()
    monkeypatch.setattr(actions, 'analyze_session', fail)
    response = await actions.end_session(request(), sid, data.admin, data.db)
    assert response.status_code == 200
    assert '완료' in response.body.decode()
    async with data.factory() as reader:
        assert (await reader.get(Session, sid)).ended_at is not None
        assert (await reader.scalars(select(SessionFeedbackReport))).all() == []

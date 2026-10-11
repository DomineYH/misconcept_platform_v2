"""Child recovery preserves queued frames and consumes task failures."""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)

from src.services import comparison_stream
from src.services.comparison_runs import reserve_comparison

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.mark.parametrize("queued", [0, 2])
@pytest.mark.parametrize("storage_failure", [False, True])
async def test_child_recovery_follows_queued_frames_and_logs_storage_failure(
    data, comparison, monkeypatch, caplog, queued, storage_failure
):
    from src.api.schemas.comparison import ComparisonRequest

    snapshot, executions = await reserve_comparison(
        data.factory, data.admin, ComparisonRequest.model_validate(comparison)
    )

    async def broken_stream(factory, accepted, execution, cancelled):
        for seq in range(queued):
            yield comparison_stream.sse_frame(
                accepted,
                "run.accepted" if seq == 0 else "output.delta",
                seq,
                text="queued",
            )
        raise RuntimeError("broken child")

    monkeypatch.setattr(comparison_stream, "stream_comparison", broken_stream)

    def reject(conn, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE generation_run"):
            raise SQLAlchemyError("recovery storage failure")

    if storage_failure:
        event.listen(data.engine.sync_engine, "before_cursor_execute", reject)
    response = comparison_stream.ComparisonStreamingResponse(
        data.factory, snapshot, executions
    )
    try:
        frames = [frame async for frame in response.body_iterator]
    finally:
        if storage_failure:
            event.remove(
                data.engine.sync_engine, "before_cursor_execute", reject
            )
    assert len(frames) == max(1, queued) + 1
    assert '"seq": 0' in frames[0]
    assert f'"seq": {max(1, queued)}' in frames[-1]
    assert frames[-1].startswith(
        "event: run.failed" if storage_failure else "event: run.interrupted"
    )
    if queued:
        assert '"text": "queued"' in frames[1]
    if storage_failure:
        assert any(
            record.message == "Comparison child recovery failed"
            and record.exc_info
            for record in caplog.records
        )
    assert not comparison_stream.active_runs


async def test_recover_task_exception_is_retrieved(
    data, comparison, monkeypatch, caplog
):
    from src.api.schemas.comparison import ComparisonRequest

    snapshot, executions = await reserve_comparison(
        data.factory, data.admin, ComparisonRequest.model_validate(comparison)
    )

    async def broken_stream(*args):
        raise RuntimeError("broken child")
        yield

    async def broken_recovery(self, child):
        raise RuntimeError("recovery queue failure")

    monkeypatch.setattr(comparison_stream, "stream_comparison", broken_stream)
    monkeypatch.setattr(
        comparison_stream.ComparisonStreamingResponse,
        "recover",
        broken_recovery,
    )
    response = comparison_stream.ComparisonStreamingResponse(
        data.factory, snapshot, executions
    )
    assert [frame async for frame in response.body_iterator] == []
    assert any(
        record.message == "Comparison recovery task failed" and record.exc_info
        for record in caplog.records
    )
    assert not comparison_stream.active_runs

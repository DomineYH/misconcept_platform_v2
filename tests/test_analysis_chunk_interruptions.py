"""Retry, admission and interruption preserve the durable analysis contract."""

import json

import httpx2
import pytest
from sqlalchemy import select
from test_analysis_chunks import chunk_reply, confirm
from test_analysis_invocations import USAGE, analysis_transport
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_student_probe import response_body

from src.models import ApiUsageLog

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize("retry_at", [1, 3])
async def test_transient_retry_is_a_separate_attempt_within_one_chunk_or_merge(
    data, api, monkeypatch, retry_at
):
    await long_session(data, monkeypatch)

    async def upstream(request, body):
        if len(calls) == retry_at:
            raise httpx2.ConnectError("PRIVATE NETWORK ERROR", request=request)
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, body = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    assert result["latest_run"]["status"] == "ok"
    assert len(calls) == plan["generation_calls"] + 1 == 4
    assert calls[retry_at - 1] == calls[retry_at]
    async with data.factory() as db:
        ledger = list(
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        )
    failed, retried = ledger[retry_at - 1 : retry_at + 1]
    assert (failed.status, failed.error_code, failed.attempt_no) == (
        "failed",
        "transient",
        1,
    )
    assert (retried.status, retried.attempt_no) == ("completed", 2)
    assert failed.invocation_id == retried.invocation_id
    assert len({r.invocation_id for r in ledger}) == 3
    assert all(
        r.run_id == accepted["run_id"] and r.request_id == body["request_id"]
        for r in ledger
    )
    assert all(c.is_closed() for c in clients)


async def test_remaining_run_deadline_caps_each_call_and_prevents_adoption(
    data, api, monkeypatch
):
    import asyncio
    from datetime import timedelta

    from src.models import AppSetting, GenerationRun
    from src.models.provider_connection import now
    from src.services import analysis_runs, openai_generation

    await long_session(data, monkeypatch)
    setting = await data.db.get(AppSetting, 1)
    setting.timeouts_json = {**setting.timeouts_json, "analysis_total": 7}
    await data.db.commit()

    async def upstream(request, body):
        if len(calls) == 1:
            async with data.factory() as db:
                run = await db.scalar(select(GenerationRun))
                run.started_at = now() - timedelta(
                    seconds=analysis_runs.RUN_SECONDS - 2
                )
                await db.commit()
            return httpx2.Response(
                200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
            )
        await asyncio.Event().wait()

    clients, calls = analysis_transport(monkeypatch, upstream)
    sdk_factory, timeouts = openai_generation.AsyncOpenAI, []

    def record_timeout(**kwargs):
        timeouts.append(kwargs["timeout"].read)
        return sdk_factory(**kwargs)

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", record_timeout)
    accepted, _, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    assert result["latest_run"]["status"] == "failed"
    assert result["latest_run"]["error_code"] == "timeout_total"
    assert result["accepted_report"] is None
    assert len(calls) == 2 and timeouts[0] == 7 and 0 < timeouts[1] <= 2
    async with data.factory() as db:
        ledger = list(
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        )
    assert [(r.operation, r.status) for r in ledger] == [
        ("analysis_chunk", "completed"),
        ("analysis_chunk", "timed_out"),
    ]
    assert all(c.is_closed() for c in clients)


async def test_current_slot_limit_stops_next_chunk_without_queueing(
    data, api, monkeypatch
):
    import asyncio

    from src.models import AppSetting, ModelConfig, ProviderConnection
    from src.services.call_admission import admit_call
    from src.services.lesson_snapshots import read_lesson_snapshot

    await long_session(data, monkeypatch)
    connection = await data.db.scalar(select(ProviderConnection))
    model = await data.db.scalar(select(ModelConfig))
    options = read_lesson_snapshot(
        data.session
    ).config.analysis.resolved_model_config.options.model_dump(
        exclude_unset=True
    )
    release, ready, holders = asyncio.Event(), asyncio.Queue(), []

    async def hold_slot():
        permit = await admit_call(
            data.factory,
            connection_id=connection.id,
            owner_id=data.owner.id,
            operation="analysis_chunk",
            role="analysis",
            admin=False,
            model_config_id=model.id,
            expected_model_version=model.config_version,
            model_options=options,
        )
        ready.put_nowait(None)
        try:
            await release.wait()
        finally:
            permit.release()

    async def upstream(request, body):
        for _ in range(2):
            holders.append(asyncio.create_task(hold_slot()))
        # The two admitted holds stay active while limits are lowered.
        for _ in range(2):
            await asyncio.wait_for(ready.get(), 5)
        async with data.factory() as db:
            setting = await db.get(AppSetting, 1)
            setting.limits_json = {
                **setting.limits_json,
                "total": 2,
                "admin": 1,
            }
            await db.commit()
        return httpx2.Response(
            200, json=response_body(json.dumps(chunk_reply(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    try:
        accepted, plan, _ = await confirm(api, data.session.id)
        result = await terminal(api, accepted["actions"]["status"])
        assert result["latest_run"]["status"] == "degraded"
        assert result["latest_run"]["error_code"] == "call_limit_reached"
        assert (
            result["accepted_report"]["coverage"]["reviewed_message_ids"]
            == plan["chunks"][0]["message_ids"]
        )
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(*holders)

"""Invalid original evidence must preserve the prior normal analysis."""

import json
from time import monotonic_ns
from uuid import uuid4

import httpx2
import pytest
from s6_measurement import write_sample
from s6_observer import Observation, input_metadata
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport, result_for
from test_analysis_runs import terminal
from test_native_analysis import native_analysis
from test_s6_workloads import REPEATS
from test_scenario_api import client, login
from test_student_probe import response_body

from src.models import ApiUsageLog, AppSetting

__all__ = ["client"]


@pytest.mark.parametrize("repeat", REPEATS)
async def test_invalid_evidence_reanalysis_preserves_normal_report_and_replays(
    data, client, monkeypatch, repeat
):
    await native_analysis(data, monkeypatch)
    observation = Observation(
        "analysis_invalid_evidence",
        repeat,
        {"fixture": "native_analysis", "fault": "quote_absent_from_original"},
        limits=(await data.db.get(AppSetting, 1)).limits_json,
    )
    observation.conditions["mock_body_delay_ms"] = None
    invalid = False

    async def upstream(request, body):
        value = result_for(body)
        if invalid:
            item = value["message_classifications"][0]
            value["strengths"] = [
                dict(
                    message_id=item["message_id"],
                    quote="Fabricated original evidence",
                    reason="Synthetic reason",
                )
            ]
        call = dict(
            start=monotonic_ns(),
            first_body=None,
            end=None,
            **input_metadata(body),
            model_options={
                key: body[key]
                for key in ("model", "max_output_tokens", "reasoning")
            },
        )
        observation.calls.append(call)
        response = httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )
        call["end"] = monotonic_ns()
        return response

    clients, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.owner)
    accepted = await client.post(
        f"/sessions/{data.session.id}/analyze",
        json=dict(request_id=str(uuid4())),
    )
    assert accepted.status_code == 202
    original = await terminal(client, accepted.json()["actions"]["status"])
    assert original["latest_run"]["status"] == "ok"
    assert len(calls) == 1
    invalid = True
    login(client, data.admin)
    path = f"/admin/sessions/{data.session.id}/analyze_regenerate"
    payload = dict(request_id=str(uuid4()))
    start = monotonic_ns()
    accepted = await client.post(path, json=payload)
    assert accepted.status_code == 202
    result = await terminal(client, accepted.json()["actions"]["status"])
    end = monotonic_ns()
    assert result["latest_run"]["status"] == "degraded"
    assert result["latest_run"]["error_code"] == "invalid_reference"
    assert result["latest_run"]["preserved"] is True
    assert result["accepted_report"] == original["accepted_report"]
    assert (await client.post(path, json=payload)).status_code == 200
    assert len(calls) == 2 and all(sdk.is_closed() for sdk in clients)
    async with data.factory() as db:
        ledger = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert len(ledger) == 2 and all(entry.attempt_no == 1 for entry in ledger)
    assert ledger[-1].error_code == "invalid_reference"
    row = observation.record(
        "failed",
        start,
        end,
        ledger=ledger[-1:],
        calls=observation.calls[-1:],
        final=True,
    )
    row["conditions"]["terminal_status"] = result["latest_run"]["status"]
    row["conditions"]["error_code"] = result["latest_run"]["error_code"]
    row["unknown_reason"] += "; final includes 20ms polling overhead"
    write_sample(row)

"""Failure/retry policy at the runtime analysis HTTP and SDK transport seams."""

import json

import httpx2
import pytest
from sqlalchemy import select
from test_analysis_invocations import (
    USAGE,
    analysis_transport,
    prepare_analysis,
    result_for,
)
from test_analysis_invocations import (
    api as analysis_api,
)
from test_provider_connections import api as provider_api
from test_student_probe import response_body

from src.models import ApiUsageLog

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


class PartialBody(httpx2.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"PRIVATE-PARTIAL-BODY":'
        raise httpx2.ReadError("Synthetic interrupted response body")


async def test_analysis_never_replays_a_partially_received_body(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        if body["text"]["format"]["name"] == "RuntimeClassification":
            return httpx2.Response(200, stream=PartialBody())
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
    async with data.factory() as db:
        rows = (
            await db.scalars(
                select(ApiUsageLog).where(
                    ApiUsageLog.operation == "classification"
                )
            )
        ).all()
    assert len(rows) == 1 and rows[0].status == "failed"
    assert rows[0].error_code == "transient" and rows[0].input_tokens is None
    assert "PRIVATE-PARTIAL-BODY" not in response.text


@pytest.mark.parametrize(
    "operation", ["classification", "synthesis", "greeting"]
)
@pytest.mark.parametrize(
    "mode,code,retry",
    [
        ("connect", "transient", True),
        ("408", "transient", True),
        ("409", "transient", True),
        ("503", "transient", True),
        ("429", "rate_limited", True),
        ("quota", "quota", False),
        ("401", "authentication", False),
        ("403", "permission", False),
        ("json", "invalid_json", False),
        ("refused", "refused", False),
        ("limit", "output_limit", False),
        ("empty", "empty_response", False),
        ("body_error", "transient", False),
    ],
)
async def test_analysis_records_every_failure_and_only_safe_retries(
    data, api, monkeypatch, operation, mode, code, retry
):
    await prepare_analysis(data, monkeypatch)
    names = {
        "greeting": "RuntimeGreetings",
        "classification": "RuntimeClassification",
        "synthesis": "RuntimeSynthesis",
    }

    async def upstream(request, body):
        if body["text"]["format"]["name"] != names[operation]:
            return httpx2.Response(
                200, json=response_body(json.dumps(result_for(body)), USAGE)
            )
        if mode == "connect":
            raise httpx2.ConnectError(
                "Synthetic connect failure", request=request
            )
        if mode.isdigit() or mode == "quota":
            status = 429 if mode == "quota" else int(mode)
            error = (
                "insufficient_quota" if mode == "quota" else "synthetic_error"
            )
            return httpx2.Response(
                status,
                json={"error": {"code": error}},
                headers={"Retry-After": "0.01"},
            )
        value = response_body("" if mode == "empty" else "not json", USAGE)
        if mode == "refused":
            value["output"][0]["content"] = [
                {"type": "refusal", "refusal": "PRIVATE-REFUSAL"}
            ]
        if mode == "limit":
            value.update(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        if mode == "body_error":
            value.update(
                status="failed",
                error={"code": "server_error", "message": "PRIVATE-ERROR"},
            )
        return httpx2.Response(200, json=value)

    clients, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    attempts = 2 if retry and operation != "greeting" else 1
    assert len(calls) == attempts + 2 and all(c.is_closed() for c in clients)
    async with data.factory() as db:
        rows = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
    assert len(rows) == len(calls)
    failures = [r for r in rows if r.operation == operation]
    assert [r.attempt_no for r in failures] == list(range(1, attempts + 1))
    assert all(
        r.error_code == code and r.finished_at is not None for r in failures
    )
    assert len({r.invocation_id for r in failures}) == 1
    assert len({r.request_id for r in rows}) == 1
    assert failures[0].retry_wait_ms is None
    if attempts == 2:
        expected_wait = 1000 if mode == "connect" else 10
        assert failures[1].retry_wait_ms == expected_wait
        assert (
            failures[1].started_at - failures[0].finished_at
        ).total_seconds() >= expected_wait / 1000
    if mode in ("json", "refused", "limit", "empty", "body_error"):
        assert all(r.total_tokens == 15 for r in failures)
    else:
        assert all(
            r.input_tokens is None and r.estimated_cost_usd is None
            for r in failures
        )
    assert (
        "PRIVATE-REFUSAL" not in response.text
        and "PRIVATE-ERROR" not in response.text
    )

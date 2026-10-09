"""Price persistence and attempt sums through authenticated HTTP and SDK parsing."""

import json
from contextlib import aclosing
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import text
from test_call_execution import consume, ordinary, verified_model
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import response_body, sdk_transport, sse

from src.services.call_execution import execute_call

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_priced_retry_and_cumulative_stream_have_durable_attempt_sums(
    data, api, monkeypatch
):
    fixture = json.loads(Path("tests/fixtures/usage_pricing.json").read_text())[
        "openai"
    ]
    request = await verified_model(data, api, monkeypatch)

    async def upstream(request, payload):
        # The durable attempt must precede every SDK request, including the retry.
        async with data.engine.connect() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM api_usage_log WHERE status='running'"
                    )
                )
                == 1
            )
        if len(calls) == 1:
            return httpx2.Response(
                429,
                headers={"retry-after": "0"},
                json={
                    "error": {"type": "rate_limit_error", "message": "PRIVATE"}
                },
            )
        final = response_body(usage=fixture["usage"])
        final["service_tier"] = "default"
        if not payload.get("stream"):
            return httpx2.Response(200, json=final)
        first = response_body(
            usage={**fixture["usage"], "output_tokens": 10, "total_tokens": 110}
        )
        first.update(service_tier="default", status="in_progress")
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.in_progress", response=first, sequence_number=0
            )
            + sse(
                "response.output_text.delta",
                delta="Visible",
                sequence_number=1,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse("response.completed", response=final, sequence_number=2),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    events = await consume(await ordinary(data, request), request)
    assert events[-1].type == "completed"
    async with aclosing(
        execute_call(await ordinary(data, request), request, kind="stream")
    ) as stream:
        events = [event async for event in stream]
    assert events[-1].type == "completed"
    assert len(calls) == 3 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT invocation_id,attempt_no,status,total_tokens,estimated_cost_usd,pricing_as_of,pricing_source "
                    "FROM api_usage_log WHERE operation='classification' ORDER BY id"
                )
            )
        ).all()
        assert len(rows) == 3
        assert rows[0][0] == rows[1][0] and rows[2][0] != rows[1][0]
        assert [r[1:5] for r in rows] == [
            (1, "failed", None, None),
            (2, "completed", 120, 0.000056),
            (1, "completed", 120, 0.000056),
        ]
        assert all(
            r[5] == "2026-10-09" and r[6].endswith("/gpt-5-mini")
            for r in rows[1:]
        )
    dashboard = await api.get("/admin/api-usage")
    assert dashboard.status_code == 200
    for field, expected in [
        ("known-cost", "$0.000112"),
        ("unpriced-attempts", "3"),
        ("model-list-calls", "0"),
    ]:
        assert (
            dashboard.text.split(f'id="{field}">')[1].split("</dd>")[0].strip()
            == expected
        )
    assert "PRIVATE" not in dashboard.text

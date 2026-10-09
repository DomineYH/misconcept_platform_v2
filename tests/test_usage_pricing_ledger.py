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


@pytest.mark.parametrize(
    "case,expected_output,expected_cost",
    [
        ("google_missing_thoughts", None, None),
        ("anthropic_zero_write", 8, 0.0001509),
        ("anthropic_unknown_tier", 8, None),
        ("anthropic_unknown_write_ttl", 8, None),
    ],
)
async def test_provider_unknowns_and_zero_writes_reach_dashboard(
    data, api, monkeypatch, case, expected_output, expected_cost
):
    from uuid import uuid4

    from test_model_management import write
    from test_provider_connections import KEY, PASSWORD
    from test_role_probes import JUDGMENT
    from test_student_probe import completed

    if case.startswith("google"):
        import httpx
        from test_google_catalog import install
        from test_google_invocations import response
        from test_google_probes import prepare

        body = await prepare(api, "mentor")

        async def upstream(request):
            value = response(
                json.dumps(JUDGMENT) if len(calls) == 1 else "Coaching",
                usage={
                    "promptTokenCount": 10,
                    "cachedContentTokenCount": 0,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 14,
                },
            )
            value["modelVersion"] = "gemini-2.5-flash"
            return httpx.Response(200, json=value)

        clients, calls = install(monkeypatch, upstream)
    else:
        from test_anthropic_catalog import sdk_transport as claude_transport
        from test_anthropic_probes import prepare
        from test_anthropic_probes import response_body as claude_body

        await prepare(api)
        body = dict(expected_version=1, role="mentor", request_id=str(uuid4()))

        async def upstream(request):
            value = claude_body(
                json.dumps(JUDGMENT) if len(calls) == 1 else "Coaching"
            )
            value["usage"].update(
                cache_creation_input_tokens=(
                    2 if case.endswith("write_ttl") else 0
                ),
                service_tier=None if case.endswith("tier") else "standard",
                inference_geo="global",
            )
            return httpx2.Response(200, json=value)

        clients, calls = claude_transport(
            monkeypatch, upstream, "anthropic_generation"
        )
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert len(calls) == 2
    assert all(
        c.is_closed if case.startswith("google") else c.is_closed()
        for c in clients
    )
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT output_tokens,estimated_cost_usd,pricing_as_of FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
    assert [r[0] for r in rows] == [expected_output] * 2
    for row in rows:
        assert row[1] == (
            None if expected_cost is None else pytest.approx(expected_cost)
        )
        assert row[2] == (None if expected_cost is None else "2026-10-09")
    dashboard = await api.get("/admin/api-usage")
    assert dashboard.status_code == 200
    expected_count = "2" if expected_cost is None else "0"
    assert (
        dashboard.text.split('id="unpriced-attempts">')[1]
        .split("</dd>")[0]
        .strip()
        == expected_count
    )
    if expected_cost is None:
        assert "산정 불가" in dashboard.text
    else:
        assert (
            dashboard.text.split('id="known-cost">')[1]
            .split("</dd>")[0]
            .strip()
            == "$0.000302"
        )
    assert all(
        secret not in dashboard.text
        for secret in (KEY, PASSWORD, "PRIVATE-THINKING")
    )


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

"""API usage screens through authenticated HTTP and isolated SQLite."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from test_scenario_api import client as client_fixture
from test_scenario_api import login

from src.api.dependencies import templates
from src.models.api_usage import ApiUsageLog

client = client_fixture


async def test_dashboard_displays_recorded_price_source_without_raw_usage(
    data, client
):
    data.db.add(
        ApiUsageLog(
            invocation_id="priced-probe",
            attempt_no=1,
            session_id=None,
            provider="openai",
            model="gpt-5-mini",
            role="student",
            operation="probe",
            status="completed",
            estimated_cost_usd=0.000056,
            pricing_as_of="2026-10-09",
            pricing_source="https://developers.openai.com/api/docs/models/gpt-5-mini",
            raw_usage_json={"private": "PRIVATE-USAGE-SENTINEL"},
        )
    )
    await data.db.commit()
    login(client, data.admin)
    response = await client.get("/admin/api-usage")
    assert response.status_code == 200
    assert (
        "https://developers.openai.com/api/docs/models/gpt-5-mini"
        in response.text
    )
    assert "2026-10-09" in response.text and "$0.000056" in response.text
    assert "PRIVATE" not in response.text


async def test_admin_can_read_preserved_legacy_usage(data, client):
    data.db.add(
        ApiUsageLog(
            session_id=data.session.id,
            bot_type="tutor",
            model="legacy-model",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
            estimated_cost_usd=0.012345,
            timestamp=datetime(2026, 1, 1),
            operation="greeting",
        )
    )
    await data.db.commit()
    login(client, data.admin)
    response = await client.get("/admin/api-usage")
    assert response.status_code == 200
    assert "legacy-model" in response.text
    assert "greeting" in response.text
    assert "$0.012345" in response.text


def test_usage_fixture_distinguishes_unknown_and_zero_without_secrets():
    fixture = json.loads(Path("tests/fixtures/api_usage.json").read_text())
    fixture["logs"][0].update(
        api_key="PRIVATE KEY SENTINEL",
        prompt="PRIVATE PROMPT SENTINEL",
        response="PRIVATE RESPONSE SENTINEL",
    )
    html = templates.get_template("admin/api_usage.html").render(
        user=SimpleNamespace(nickname="Admin", role="admin"), **fixture
    )
    assert "알려진 추정 비용 합계" in html
    assert "$0.375000" in html
    assert "$0.000000" in html
    assert "산정 불가" in html
    assert "알 수 없음" in html
    assert "기존 기록" in html
    assert "PRIVATE" not in html


async def test_legacy_summary_excludes_lists_and_preserves_cost_precision(
    data, client
):
    for operation, price in [
        ("student", 0.012345),
        (None, 0.125),
        ("model_list", 9.0),
    ]:
        data.db.add(
            ApiUsageLog(
                session_id=data.session.id,
                bot_type="student",
                model=operation or "legacy-model",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=price,
                operation=operation,
            )
        )
    await data.db.commit()
    login(client, data.admin)
    response = await client.get("/admin/api-usage")
    assert response.status_code == 200
    summary = response.text.split('id="known-cost">')[1].split("</dd>")[0]
    assert summary.strip() == "$0.137345"
    assert "전체 시도 수와 미산정 건수를 확인할 수 없습니다" in response.text


async def test_usage_dashboard_requires_admin_and_handles_empty_history(
    data, client
):
    assert (await client.get("/admin/api-usage")).status_code == 303
    login(client, data.owner)
    assert (await client.get("/admin/api-usage")).status_code == 403
    login(client, data.admin)
    response = await client.get("/admin/api-usage")
    assert response.status_code == 200
    assert "$0.000000" in response.text
    assert "기록이 없습니다" in response.text
    assert "알 수 없음" in response.text


async def test_summary_counts_whole_ledger_attempts_and_preserves_history(
    data, client
):
    rows = [
        dict(invocation_id="retry", attempt_no=1, status="failed"),
        dict(invocation_id="retry", attempt_no=2, estimated_cost_usd=0.25),
        dict(invocation_id="unknown", attempt_no=1),
        dict(invocation_id="list", attempt_no=1, operation="model_list"),
        dict(
            invocation_id="list-priced",
            attempt_no=1,
            operation="model_list",
            estimated_cost_usd=9.0,
        ),
        dict(estimated_cost_usd=0.012345, operation=None),
        dict(estimated_cost_usd=None, operation=None),
    ]
    for index, row in enumerate(rows):
        values = dict(
            model=f"old-{index}",
            operation="student",
            timestamp=datetime(2026, 1, 1),
        )
        values.update(row)
        data.db.add(ApiUsageLog(**values))
    for index in range(100):
        data.db.add(
            ApiUsageLog(
                invocation_id=f"zero-{index}",
                attempt_no=1,
                operation="student",
                estimated_cost_usd=0.0,
                timestamp=datetime(2026, 10, 9),
            )
        )
    await data.db.commit()
    login(client, data.admin)
    for _ in range(2):
        response = await client.get("/admin/api-usage")
        assert response.status_code == 200
        for field, expected in [
            ("known-cost", "$0.262345"),
            ("unpriced-attempts", "2"),
            ("model-list-calls", "2"),
        ]:
            value = response.text.split(f'id="{field}">')[1].split("</dd>")[0]
            assert value.strip() == expected
        assert "old-" not in response.text
        assert (
            "전체 시도 수와 미산정 건수를 확인할 수 없습니다" in response.text
        )
    await data.db.refresh(data.session)
    # Reads must not rewrite old prices or invent a historical pricing date.
    from sqlalchemy import select

    saved = (
        await data.db.scalars(
            select(ApiUsageLog).where(ApiUsageLog.model == "old-5")
        )
    ).one()
    assert saved.estimated_cost_usd == 0.012345
    assert saved.pricing_as_of is None and saved.pricing_source is None

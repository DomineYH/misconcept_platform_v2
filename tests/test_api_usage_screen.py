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

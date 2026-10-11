"""Live model-copy validation stays read-only at the comparison HTTP seam."""

import json
import re
from datetime import timedelta

import pytest
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ComparisonRun

__all__ = ["client", "comparison", "scenario_payload", "student"]


async def test_model_copy_uses_frozen_options_and_current_registration(
    data, client, student, comparison
):
    login(client, data.admin)
    started = await client.post("/admin/comparisons", json=comparison)
    path = f"/admin/comparisons/{frames(started)[0][1]['comparison_run_id']}"
    before = (await client.get(path)).json()
    student.model.default_options_json = {"max_output_tokens": 9999}
    await data.db.commit()
    result = await client.get(f"{path}/panels/1/model")
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json() == {
        "target_role": "student",
        "model_config": comparison["panels"][0]["model_config"],
    }
    assert (await client.get(path)).json() == before
    student.responses.create.assert_awaited_once()
    student.connection.enabled = False
    await data.db.commit()
    assert (await client.get(f"{path}/panels/1/model")).status_code == 422
    assert (await client.get(path)).json() == before


@pytest.mark.parametrize(
    "state,expected",
    [
        ("running", 409),
        ("role_stale", 422),
        ("options_invalid", 422),
        ("identity_changed", 422),
        ("other_admin", 404),
        ("expired", 404),
        ("missing_panel", 404),
        ("anonymous", 401),
    ],
)
async def test_model_copy_refusals_are_read_only(
    data, client, student, comparison, monkeypatch, state, expected
):
    from src.services import model_capabilities

    login(client, data.admin)
    started = await client.post("/admin/comparisons", json=comparison)
    comparison_id = frames(started)[0][1]["comparison_run_id"]
    path = f"/admin/comparisons/{comparison_id}"
    panel_id = "missing" if state == "missing_panel" else "1"
    if state in ("running", "expired"):
        async with data.factory() as db:
            record = await db.get(ComparisonRun, comparison_id)
            if state == "running":
                record.status = "running"
            else:
                record.expires_at = record.created_at - timedelta(seconds=1)
            await db.commit()
    elif state == "role_stale":
        student.model.verification_state = {}
    elif state == "options_invalid":
        monkeypatch.setitem(
            model_capabilities.DEFINITIONS,
            student.model.model_id,
            (["low"], "low", False),
        )
    elif state == "identity_changed":
        student.model.model_id = "gpt-5.2"
    elif state == "other_admin":
        data.other.role = "admin"
    await data.db.commit()
    before = await client.get(path)
    if state == "other_admin":
        login(client, data.other)
    elif state == "anonymous":
        client.cookies.clear()
    response = await client.get(f"{path}/panels/{panel_id}/model")
    assert response.status_code == expected
    assert "sk-PRIVATE" not in response.text
    student.responses.create.assert_awaited_once()
    login(client, data.admin)
    after = await client.get(path)
    assert after.status_code == before.status_code
    assert after.json() == before.json()


def editor_data(response):
    return json.loads(
        re.search(
            r'<script[^>]*id="scenario-editor-data"[^>]*>(.*?)</script>',
            response.text,
            re.S,
        )[1]
    )


async def test_past_comparison_opens_an_explicit_unsaved_draft(
    data, client, student, comparison
):
    from test_comparison_student import classroom_counts

    login(client, data.admin)
    comparison["source_snapshot"]["title"] = "Frozen draft title"
    started = await client.post("/admin/comparisons", json=comparison)
    comparison_id = frames(started)[0][1]["comparison_run_id"]
    before = await classroom_counts(data.db)
    path = f"/admin/comparisons/{comparison_id}"
    frozen = (await client.get(path)).json()
    response = await client.get(
        f"/admin/scenarios/new?from_comparison={comparison_id}"
    )
    assert response.status_code == 200
    draft = editor_data(response)
    assert draft["id"] is None and draft["status"] == "draft"
    assert draft["comparison_draft_id"] == comparison_id
    assert draft["title"] == "Frozen draft title"
    assert draft["config"] == frozen["source_snapshot"]["config"]
    assert await classroom_counts(data.db) == before
    assert (await client.get(path)).json() == frozen
    login(client, data.owner)
    assert (
        await client.get(
            f"/admin/scenarios/new?from_comparison={comparison_id}"
        )
    ).status_code == 403
    login(client, data.admin)
    async with data.factory() as db:
        record = await db.get(ComparisonRun, comparison_id)
        record.expires_at = record.created_at - timedelta(seconds=1)
        await db.commit()
    assert (
        await client.get(
            f"/admin/scenarios/new?from_comparison={comparison_id}"
        )
    ).status_code == 404

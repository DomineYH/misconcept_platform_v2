"""Saved analysis compatibility through authenticated record and export routes."""

import csv
import io
import json
from datetime import datetime
from uuid import uuid4

import pytest
from s4_record_fixtures import stored_result
from sqlalchemy import select
from test_native_analysis import api, connection_api, native_analysis
from test_scenario_api import login

from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    QuestionAnalysis,
    SessionFeedbackReport,
    SessionSummary,
)
from src.services.lesson_snapshots import canonical_hash

__all__ = ["api", "connection_api", "stored_result"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_native_v1_partial_result_keeps_existing_teacher_retry_permission(
    data, api, monkeypatch
):
    await native_analysis(data, monkeypatch)
    data.db.add_all(
        [
            SessionSummary(
                session_id=data.session.id,
                distribution_json='{"A":1}',
                feedback="Original partial",
            ),
            SessionFeedbackReport(
                session_id=data.session.id,
                version=1,
                model="old",
                prompt_hash="old",
                status="degraded",
                payload_json='{"brief_feedback":["Original partial"]}',
            ),
        ]
    )
    await data.db.commit()
    response = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    assert response["accepted_report"]["coverage"] is None
    assert response["retryable"] is True
    assert response["permissions"]["can_retry"] is True


@pytest.mark.parametrize(
    "stored_result",
    [
        "ok",
        "chunked",
        "partial",
        "off",
        "legacy",
        "summary_only",
        "failed",
        "no_accepted",
    ],
    indirect=True,
)
async def test_saved_results_use_shared_projection_on_every_http_surface_and_keep_permissions(
    data, api, stored_result
):
    state, fixture = stored_result
    sid = data.session.id
    expected_status = 200
    response = await api.get(f"/sessions/{sid}/analysis")
    assert response.status_code == expected_status
    if expected_status == 200:
        public = response.json()
        assert [m["id"] for m in public["messages"]] == [
            m["id"] for m in fixture["messages"]
        ]
        accepted = public["accepted_report"]
        if state == "no_accepted":
            assert (
                accepted is None and public["latest_run"]["status"] == "ready"
            )
            assert public["plan"]["mode"] == "single"
            assert public["plan"]["message_ids"] == [
                m["id"]
                for m in fixture["messages"]
                if m["role"] in {"teacher", "student"}
            ]
            assert list(await data.db.scalars(select(GenerationRun))) == []
            assert list(await data.db.scalars(select(ApiUsageLog))) == []
        elif state == "failed":
            assert (
                accepted is None and public["latest_run"]["status"] == "failed"
            )
        else:
            assert (
                accepted["brief_feedback"]
                == fixture["accepted_report"]["brief_feedback"]
            )
            assert (
                accepted["coverage"] == fixture["accepted_report"]["coverage"]
            )
        assert "PRIVATE" not in response.text
    for path in ["analysis_page", "analysis_modal"]:
        page = await api.get(f"/sessions/{sid}/{path}")
        assert page.status_code == expected_status
        if expected_status == 200:
            assert f'data-result-url="/sessions/{sid}/analysis"' in page.text
    history = await api.get(f"/sessions/{sid}")
    assert history.status_code == 200
    if expected_status == 200:
        assert f'data-result-url="/sessions/{sid}/analysis"' in history.text
    csv_response = await api.get(f"/sessions/{sid}/export.csv")
    assert csv_response.status_code == 200
    assert list(
        csv.DictReader(io.StringIO(csv_response.text.lstrip("\ufeff")))
    )[0]["analysis_status"] == (
        "unknown"
        if state == "no_accepted"
        else (
            "failed"
            if state == "failed"
            else (
                "legacy"
                if state in {"legacy", "summary_only"}
                else "degraded" if state == "partial" else "ok"
            )
        )
    )
    login(api, data.other)
    for path in [
        f"/sessions/{sid}",
        f"/sessions/{sid}/analysis",
        f"/sessions/{sid}/analysis_page",
        f"/sessions/{sid}/analysis_modal",
        f"/sessions/{sid}/export.csv",
        f"/admin/sessions/{sid}/analysis",
        f"/admin/sessions/{sid}/download",
        "/admin/sessions/export",
    ]:
        assert (await api.get(path)).status_code == 403
    login(api, data.admin)
    admin = await api.get(f"/admin/sessions/{sid}/analysis")
    assert admin.status_code == expected_status
    if expected_status == 200:
        assert admin.json()["accepted_report"] == public["accepted_report"]
    for path in ["analysis_modal", "detail"]:
        modal = await api.get(f"/admin/sessions/{sid}/{path}")
        assert modal.status_code == (
            200 if path == "detail" else expected_status
        )
        if expected_status == 200:
            assert (
                f'data-result-url="/admin/sessions/{sid}/analysis"'
                in modal.text
            )
        assert "PRIVATE" not in modal.text
    analysis_id = await data.db.scalar(select(QuestionAnalysis.id))
    if analysis_id is not None:
        detail = await api.get(f"/admin/analysis/{analysis_id}/detail")
        assert (
            f'data-result-url="/admin/sessions/{sid}/analysis"' in detail.text
        )
    for path in [
        f"/admin/sessions/{sid}/download",
        "/admin/sessions/export",
        f"/admin/user-conversations/{data.owner.id}/export",
    ]:
        exported = await api.get(path)
        assert exported.status_code == 200 and "PRIVATE" not in exported.text
        assert (
            list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))[
                0
            ]["analysis_status"]
            == list(
                csv.DictReader(io.StringIO(csv_response.text.lstrip("\ufeff")))
            )[0]["analysis_status"]
        )
    exported = await api.post(
        "/admin/sessions/export-selected",
        data={"session_ids": str(sid)},
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert exported.status_code == 200 and "PRIVATE" not in exported.text


@pytest.mark.parametrize("stored_result", ["no_accepted"], indirect=True)
@pytest.mark.parametrize("absence", ["legacy", "open_native"])
async def test_unplannable_session_without_saved_result_remains_unavailable(
    data, api, stored_result, absence
):
    if absence == "legacy":
        data.session.snapshot_origin = "legacy_reconstructed"
    else:
        data.session.ended_at = None
    await data.db.commit()
    sid = data.session.id
    expected_status = 404 if absence == "legacy" else 400
    for path in ["analysis", "analysis_page", "analysis_modal"]:
        assert (
            await api.get(f"/sessions/{sid}/{path}")
        ).status_code == expected_status
    login(api, data.admin)
    for path in ["analysis", "analysis_modal"]:
        assert (
            await api.get(f"/admin/sessions/{sid}/{path}")
        ).status_code == expected_status


@pytest.mark.parametrize("structured", [False, True])
async def test_historical_results_share_projection_without_inventing_coverage(
    data, api, monkeypatch, structured
):
    await native_analysis(data, monkeypatch)
    data.session.snapshot_origin = "legacy_reconstructed"
    data.session.source_scenario_version = None
    data.session.config_snapshot_json = {
        **data.session.config_snapshot_json,
        "unknown_fields": ["config"],
    }
    data.session.config_hash = canonical_hash(data.session.config_snapshot_json)
    timestamp = datetime(2025, 1, 2, 3, 4, 5)
    message = Message(
        session_id=data.session.id,
        role="teacher",
        content="Historical question",
        created_at=timestamp,
    )
    data.db.add(message)
    await data.db.flush()
    data.db.add_all(
        [
            QuestionAnalysis(
                message_id=message.id,
                label="Original label",
                grade="우수",
                confidence=0.8,
                meta_json='{"summary":"Original reasoning"}',
            ),
            SessionSummary(
                session_id=data.session.id,
                distribution_json='{"Original label":1}',
                feedback="Original summary",
                created_at=timestamp,
            ),
        ]
    )
    if structured:
        data.db.add(
            SessionFeedbackReport(
                session_id=data.session.id,
                version=1,
                model="historical",
                prompt_hash="old",
                status="ok",
                created_at=timestamp,
                payload_json=json.dumps(
                    dict(
                        brief_feedback=["Original feedback"],
                        strengths=[],
                        improvements=[],
                        dialogue_coaching=[],
                    )
                ),
            )
        )
    await data.db.commit()
    sid = data.session.id
    result = (await api.get(f"/sessions/{sid}/analysis")).json()
    accepted = result["accepted_report"]
    assert accepted["schema_version"] == 1 and accepted["status"] == "legacy"
    assert (
        accepted["coverage"] is None
        and accepted["misconception_findings"] == []
    )
    assert accepted["message_classifications"] == []
    assert accepted["created_at"] == "2025-01-02T03:04:05"
    assert accepted["brief_feedback"] == [
        "Original feedback" if structured else "Original summary"
    ]
    assert result["latest_run"] is None
    question = next(
        q for q in result["questions"] if q["content"] == "Historical question"
    )
    assert question["message_id"] == message.id
    assert (
        question["label"],
        question["grade"],
        question["reasoning"]["summary"],
        question["created_at"],
    ) == ("Original label", "우수", "Original reasoning", timestamp.isoformat())
    assert accepted["classification_enabled"] is None
    assert (
        result["permissions"]["read_only"]
        and not result["permissions"]["can_retry"]
    )
    for path in ["analysis_page", "analysis_modal"]:
        page = await api.get(f"/sessions/{sid}/{path}")
        assert f'data-result-url="/sessions/{sid}/analysis"' in page.text
    login(api, data.admin)
    assert (await api.get(f"/admin/sessions/{sid}/analysis")).json()[
        "accepted_report"
    ] == accepted
    for path in ["analysis_modal", "detail"]:
        page = await api.get(f"/admin/sessions/{sid}/{path}")
        assert f'data-result-url="/admin/sessions/{sid}/analysis"' in page.text
    rejected = await api.post(
        f"/admin/sessions/{sid}/analyze_regenerate",
        json={"request_id": str(uuid4())},
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "legacy_read_only"

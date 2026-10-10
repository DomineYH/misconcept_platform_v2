"""The same saved v2 contract exercises single and chunked record readers."""

import json
from datetime import datetime
from uuid import uuid4

import pytest
from s4_screen_fixtures import analysis_fixture
from sqlalchemy import delete
from test_native_analysis import native_analysis

from src.models import (
    GenerationRun,
    Message,
    QuestionAnalysis,
    SessionFeedbackReport,
    SessionSummary,
)
from src.services.lesson_snapshots import canonical_hash


@pytest.fixture
async def stored_result(data, monkeypatch, request):
    state = request.param
    await native_analysis(data, monkeypatch, enabled=state != "off")
    await data.db.execute(delete(Message))
    result = analysis_fixture("ok" if state == "no_accepted" else state)
    timestamp = datetime(2026, 10, 1, 10)
    messages = result["messages"]
    for message in messages:
        data.db.add(
            Message(
                session_id=data.session.id,
                created_at=timestamp,
                id=message["id"],
                role=message["role"],
                content=message["content"],
            )
        )
    await data.db.flush()
    report = result["accepted_report"] if state != "no_accepted" else None
    if state in {"legacy", "summary_only"}:
        data.session.snapshot_origin = "legacy_reconstructed"
        data.session.source_scenario_version = None
        data.session.config_snapshot_json = {
            **data.session.config_snapshot_json,
            "unknown_fields": ["config"],
        }
        data.session.config_hash = canonical_hash(
            data.session.config_snapshot_json
        )
        data.db.add(
            QuestionAnalysis(
                message_id=102,
                label="Original label",
                grade="우수",
                confidence=0.8,
                meta_json='{"summary":"Original reasoning"}',
            )
        )
    if report is not None:
        for item in report["message_classifications"]:
            if item["rubric_id"] is not None:
                item["rubric_id"] = "A"
                data.db.add(
                    QuestionAnalysis(
                        message_id=item["message_id"],
                        label="A",
                        grade="우수",
                        meta_json=json.dumps({"summary": item["reason"]}),
                    )
                )
        distribution = (
            {"A": report["distribution"][0]["count"]}
            if report["distribution"]
            else {}
        )
        if state in {"legacy", "summary_only"}:
            distribution = {"Original label": 1}
        data.db.add(
            SessionSummary(
                session_id=data.session.id,
                created_at=timestamp,
                distribution_json=json.dumps(distribution),
                feedback=report["brief_feedback"][0],
            )
        )
        if state != "summary_only":
            payload = {
                key: report[key]
                for key in (
                    "schema_version",
                    "message_classifications",
                    "misconception_findings",
                    "brief_feedback",
                    "strengths",
                    "improvements",
                    "dialogue_coaching",
                )
            }
            payload["metadata"] = dict(
                coverage=report["coverage"],
                mode="chunked" if state == "chunked" else "single",
                raw_snapshot="PRIVATE SNAPSHOT",
                criteria="PRIVATE CRITERIA",
                secret="PRIVATE KEY",
            )
            data.db.add(
                SessionFeedbackReport(
                    session_id=data.session.id,
                    created_at=timestamp,
                    version=report["schema_version"],
                    status="ok" if state == "legacy" else report["status"],
                    model="saved",
                    prompt_hash="saved",
                    payload_json=json.dumps(payload),
                )
            )
    if state == "failed":
        run_id = str(uuid4())
        data.db.add(
            GenerationRun(
                id=run_id,
                turn_id=run_id,
                session_id=data.session.id,
                owner_id=data.owner.id,
                operation="analysis",
                request_id=str(uuid4()),
                input_hash="input",
                config_hash="config",
                provider="openai",
                model="saved",
                status="failed",
                error_code="invalid_reference",
                started_at=timestamp,
                finished_at=timestamp,
                outcome_json='{"status":"failed","error_code":"invalid_reference"}',
            )
        )
    await data.db.commit()
    return state, result

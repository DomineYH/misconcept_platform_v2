"""Append-only S4 CSV compatibility through all exporter variants."""

import csv
import io
import json
from datetime import datetime

import pytest
from s4_record_fixtures import stored_result
from sqlalchemy import select

from src.models import Message, Session, SessionFeedbackReport
from src.services.export import CSVExporter

__all__ = ["stored_result"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_csv_json_and_multiline_cells_round_trip_across_bulk_sessions(
    data, stored_result
):
    report = await data.db.scalar(select(SessionFeedbackReport))
    payload = json.loads(report.payload_json)
    findings = [
        {
            "kind": "maintained",
            "claim": '=한글, "인용"\n두 번째 줄 🧑‍🏫',
            "evidence": [{"message_id": 103, "quote": "분모가 5라서 더 커요."}],
        }
    ]
    payload["misconception_findings"] = findings
    report.payload_json = json.dumps(payload, ensure_ascii=False)
    message = await data.db.get(Message, 101)
    message.content = '=수식, "인용"\n두 번째 줄\r\n끝'
    second = Session(
        scenario_id=data.session.scenario_id,
        teacher_id=data.owner.id,
        started_at=datetime(2026, 10, 2),
        ended_at=datetime(2026, 10, 2, 1),
    )
    data.db.add(second)
    await data.db.flush()
    data.db.add(
        Message(
            session_id=second.id,
            role="student",
            content='다른 세션, "보존"\n끝',
        )
    )
    await data.db.commit()
    exporter = CSVExporter()
    for method in [
        exporter.export_multiple_sessions,
        exporter.export_multiple_sessions_admin,
    ]:
        rows = list(
            csv.DictReader(
                io.StringIO(await method([data.session.id, second.id], data.db))
            )
        )
        assert len(rows) == 9
        assert rows[0]["content"] == '\'=수식, "인용"\n두 번째 줄\r\n끝'
        assert rows[-1]["content"] == '다른 세션, "보존"\n끝'
        assert rows[-1]["analysis_status"] == "unknown"
        summaries = [r for r in rows if r["role"] == "summary"]
        assert len(summaries) == 1
        assert (
            json.loads(summaries[0]["misconception_findings_json"]) == findings
        )
        assert json.loads(summaries[0]["analysis_coverage_json"])[
            "input_message_ids"
        ] == [101, 102, 103, 104, 105, 106, 107]


NEW_COLUMNS = [
    "analysis_schema_version",
    "analysis_status",
    "analysis_coverage_json",
    "misconception_findings_json",
    "message_analysis_disposition",
]
TEACHER_COLUMNS = [
    "session_id",
    "scenario_title",
    "student_hash",
    "timestamp",
    "role",
    "content",
    "label",
    "confidence",
    "feedback",
    "classification_status",
    "student_name",
    "snapshot_origin",
    "snapshot_created_at",
    "source_scenario_version",
    "config_hash_kind",
]
ADMIN_COLUMNS = [
    "session_id",
    "scenario_id",
    "scenario_title",
    "teacher_id",
    "teacher_username",
    "teacher_nickname",
    "session_started_at",
    "session_ended_at",
    "message_id",
    "message_created_at",
    "role",
    "content",
    "label",
    "confidence",
    "meta_json",
    "feedback",
    "classification_status",
    "student_name",
    "snapshot_origin",
    "snapshot_created_at",
    "source_scenario_version",
    "config_hash_kind",
]


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
async def test_all_csv_variants_append_exact_columns_and_place_json_on_summary_only(
    data, stored_result
):
    state, fixture = stored_result
    sid = data.session.id
    exporter = CSVExporter()
    for method, argument, original_columns in [
        (exporter.export_session, sid, TEACHER_COLUMNS),
        (exporter.export_multiple_sessions, [sid], TEACHER_COLUMNS),
        (exporter.export_session_admin, sid, ADMIN_COLUMNS),
        (exporter.export_multiple_sessions_admin, [sid], ADMIN_COLUMNS),
    ]:
        content = await method(argument, data.db)
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert reader.fieldnames == original_columns + NEW_COLUMNS
        accepted = (
            fixture["accepted_report"] if state != "no_accepted" else None
        )
        assert len(rows) == len(fixture["messages"]) + (accepted is not None)
        assert [r["content"] for r in rows if r["role"] != "summary"] == [
            m["content"] for m in fixture["messages"]
        ]
        status = {
            "partial": "degraded",
            "legacy": "legacy",
            "summary_only": "legacy",
            "failed": "failed",
            "no_accepted": "unknown",
        }.get(state, "ok")
        assert {row["analysis_status"] for row in rows} == {status}
        assert {row["classification_status"] for row in rows} == {
            (
                "classification_disabled"
                if state == "off"
                else (
                    "legacy"
                    if state in {"legacy", "summary_only"}
                    else "enabled"
                )
            )
        }
        version = (
            "1"
            if state == "legacy"
            else (
                "unknown"
                if state in {"summary_only", "failed", "no_accepted"}
                else "2"
            )
        )
        assert {row["analysis_schema_version"] for row in rows} == {version}
        classifications = (
            {
                i["message_id"]: i["disposition"]
                for i in accepted["message_classifications"]
            }
            if accepted
            else {}
        )
        for row, message in zip(rows, fixture["messages"]):
            assert (
                row["analysis_coverage_json"]
                == row["misconception_findings_json"]
                == ""
            )
            expected = ""
            if state != "off" and message["role"] == "teacher":
                expected = classifications.get(
                    message["id"],
                    "missing" if state == "partial" else "unknown",
                )
            assert row["message_analysis_disposition"] == expected
            if state in {"legacy", "summary_only"} and message["id"] == 102:
                assert row["label"] == "Original label"
                assert row["confidence"] == "0.80"
            if (
                message["id"] in classifications
                and classifications[message["id"]] == "classified"
            ):
                assert row["label"] == "Explore"
        if accepted:
            summary = rows[-1]
            assert (
                summary["role"] == "summary"
                and summary["message_analysis_disposition"] == ""
            )
            assert summary["feedback"] == accepted["brief_feedback"][0]
            if version == "2":
                assert (
                    json.loads(summary["analysis_coverage_json"])
                    == accepted["coverage"]
                )
                assert (
                    json.loads(summary["misconception_findings_json"])
                    == accepted["misconception_findings"]
                )
            else:
                assert (
                    summary["analysis_coverage_json"]
                    == summary["misconception_findings_json"]
                    == ""
                )
        assert "PRIVATE" not in content

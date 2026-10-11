"""Analysis comparisons through HTTP/SSE, SQLite and the S4 SDK fixtures."""

import copy
import json

import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply, prompt_inputs
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport
from test_comparison_student import (
    classroom_counts,
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames
from test_student_probe import response_body

from src.models import ApiUsageLog, GenerationRun

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.fixture
async def analysis_comparison(data, student, comparison):
    student.model.verification_state = {
        **student.model.verification_state,
        "analysis": {
            **student.model.verification_state["student"],
            "role_contract_version": "s4-v2",
        },
    }
    await data.db.commit()
    comparison["target_role"] = "analysis"
    comparison["source_snapshot"]["config"]["analysis"].update(
        classification_enabled=True,
        context="PRIVATE ANALYSIS CONTEXT",
        expected_understanding="Correct fractions",
        instruction="Review the full dialogue",
        rubric_name="Questions",
        rubric=[
            dict(
                id="A",
                name="Explore",
                criteria="Explore thinking",
                level="high",
            )
        ],
    )
    comparison["panels"][0]["model_config"]["options"] = {
        "max_output_tokens": 8192,
        "reasoning": {"effort": "medium"},
    }
    return comparison


async def test_analysis_panels_share_full_transcript_and_store_only_comparison_results(
    data, client, student, analysis_comparison, monkeypatch
):
    body = analysis_comparison
    body["panels"].append({**copy.deepcopy(body["panels"][0]), "panel_id": "2"})
    body["panels"][1]["model_config"]["options"]["max_output_tokens"] = 9000
    before = await classroom_counts(data.db)
    from src.services.export import CSVExporter

    exporter = CSVExporter()

    async def exported(db):
        return [
            await exporter.export_session(data.session.id, db),
            await exporter.export_session_admin(data.session.id, db),
            await exporter.export_multiple_sessions([data.session.id], db),
            await exporter.export_multiple_sessions_admin(
                [data.session.id], db
            ),
        ]

    before_csv = await exported(data.db)
    login(client, data.admin)
    before_stats = (await client.get("/admin/stats")).json()

    async def upstream(request, payload):
        from src.services.call_admission import active_calls

        assert all(p.admin and p.role == "analysis" for p in active_calls)
        inputs = prompt_inputs(payload)
        reply = analysis_reply(inputs["messages"])
        reply["strengths"] = [
            dict(message_id=1, quote="Earlier question", reason="Evidence")
        ]
        return httpx2.Response(
            200, json=response_body(json.dumps(reply), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=body)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert sorted(kind for kind, _ in events) == [
        "output.completed",
        "output.completed",
        "run.accepted",
        "run.accepted",
    ]
    path = f"/admin/comparisons/{events[0][1]['comparison_run_id']}"
    saved = (await client.get(path)).json()
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    assert calls[0]["input"] == calls[1]["input"]
    assert calls[0]["text"] == calls[1]["text"]
    assert "stream" not in calls[0]
    inputs = prompt_inputs(calls[0])
    assert inputs["messages"] == [
        dict(id=1, role="teacher", content="Earlier question"),
        dict(id=2, role="student", content="Earlier answer"),
        dict(id=3, role="teacher", content="Current question"),
    ]
    assert inputs["analysis"]["expected_understanding"] == "Correct fractions"
    assert inputs["analysis"]["rubric"][0]["criteria"] == "Explore thinking"
    for panel in saved["panels"]:
        assert panel["status"] == "ok"
        report = panel["result"]["report"]
        assert report["coverage"]["classified_message_ids"] == [1, 3]
        assert report["coverage"]["response_missing_message_ids"] == [3]
        assert report["distribution"] == [
            dict(name="Explore", count=2, percentage=100.0)
        ]
        assert panel["result"]["messages"] == inputs["messages"]
        assert panel["timing"]["first_output_ms"] is None
    assert (await client.post("/admin/comparisons", json=body)).json() == saved
    assert len(calls) == 2
    async with data.factory() as db:
        assert await classroom_counts(db) == before
        assert await exported(db) == before_csv
        runs = list(await db.scalars(select(GenerationRun)))
        assert all(
            r.operation == "analysis" and r.session_id is None for r in runs
        )
        usage = list(await db.scalars(select(ApiUsageLog)))
        assert len(usage) == 2 and all(
            u.usage_scope == "comparison"
            and u.session_id is None
            and u.attempt_no == 1
            for u in usage
        )
    assert (await client.get("/admin/stats")).json() == before_stats


@pytest.mark.parametrize(
    "boundary",
    ["complete", "complete_empty", "teacher_only", "off", "greeting"],
)
async def test_analysis_optional_last_teacher_and_classification_boundaries(
    data, client, analysis_comparison, monkeypatch, boundary
):
    body = analysis_comparison
    if boundary.startswith("complete"):
        body["current_message"] = None if boundary == "complete" else ""
    elif boundary in {"teacher_only", "greeting"}:
        body["canonical_context"] = []
        body["current_message"] = (
            "안녕하세요" if boundary == "greeting" else "Why?"
        )
    else:
        body["source_snapshot"]["config"]["analysis"][
            "classification_enabled"
        ] = False
        body["source_snapshot"]["config"]["analysis"]["rubric"] = []

    async def upstream(request, payload):
        inputs = prompt_inputs(payload)
        reply = analysis_reply(inputs["messages"], enabled=boundary != "off")
        if boundary == "greeting":
            reply["message_classifications"][0].update(
                disposition="non_analyzable", rubric_id=None
            )
        return httpx2.Response(
            200, json=response_body(json.dumps(reply), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=body)
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[0][1]["current_message_id"] == (
        None
        if boundary.startswith("complete")
        else 3 if boundary == "off" else 1
    )
    terminal = events[-1][1]
    assert terminal["outcome_status"] == "ok" and len(calls) == 1
    report = terminal["result"]["report"]
    assert report["coverage"]["response_missing_message_ids"] == (
        []
        if boundary.startswith("complete")
        else [3] if boundary == "off" else [1]
    )
    if boundary == "off":
        assert report["distribution"] == report["message_classifications"] == []
    if boundary == "greeting":
        assert report["coverage"]["non_analyzable_message_ids"] == [1]
        assert all(
            item["percentage"] is None for item in report["distribution"]
        )


@pytest.mark.parametrize(
    "failure,status,error_code",
    [
        ("unclassified", "degraded", None),
        ("missing", "degraded", "invalid_output"),
        ("quote", "degraded", "invalid_reference"),
        ("id", "degraded", "invalid_reference"),
        ("rubric", "degraded", "invalid_reference"),
        ("off_classifications", "degraded", "invalid_output"),
        ("structure", "failed", "invalid_output"),
        ("json", "failed", "invalid_json"),
        ("empty", "failed", "empty_response"),
        ("refused", "failed", "refused"),
        ("limit", "failed", "output_limit"),
        ("rate_limited", "failed", "rate_limited"),
    ],
)
async def test_analysis_results_keep_only_valid_evidence_without_repair_or_retry(
    data, client, analysis_comparison, monkeypatch, failure, status, error_code
):
    if failure == "off_classifications":
        analysis_comparison["source_snapshot"]["config"]["analysis"][
            "classification_enabled"
        ] = False

    async def upstream(request, body):
        value = analysis_reply(prompt_inputs(body)["messages"])
        item = value["message_classifications"][0]
        if failure == "unclassified":
            item.update(disposition="unclassified", rubric_id=None)
        elif failure == "missing":
            value["message_classifications"].pop(0)
        elif failure == "quote":
            item["quote"] = "INVENTED_QUOTE"
        elif failure == "id":
            item["message_id"] = 999
        elif failure == "rubric":
            item["rubric_id"] = "INVENTED_RUBRIC"
        elif failure == "structure":
            value["UNVERIFIED_JSON"] = "PRIVATE_STRUCTURED_OUTPUT"
        elif failure == "empty":
            value["brief_feedback"] = []
        if failure == "rate_limited":
            return httpx2.Response(
                429,
                headers={"Retry-After": "0"},
                json={
                    "error": {
                        "message": "PRIVATE_PROVIDER_ERROR",
                        "type": "rate_limit_error",
                    }
                },
            )
        response = response_body(
            "PRIVATE_INVALID_JSON" if failure == "json" else json.dumps(value),
            USAGE,
        )
        if failure == "refused":
            response["output"][0]["content"] = [
                dict(type="refusal", refusal="PRIVATE_REFUSAL")
            ]
        elif failure == "limit":
            response.update(
                status="incomplete",
                incomplete_details=dict(reason="max_output_tokens"),
            )
        return httpx2.Response(200, json=response)

    clients, calls = analysis_transport(monkeypatch, upstream)
    login(client, data.admin)
    response = await client.post("/admin/comparisons", json=analysis_comparison)
    events = frames(response)
    assert [kind for kind, _ in events] == [
        "run.accepted",
        "output.completed" if status == "degraded" else "run.failed",
    ]
    terminal = events[-1][1]
    assert terminal["status"] == terminal["outcome_status"] == status
    assert terminal["error_code"] == error_code
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    assert "PRIVATE_" not in json.dumps(
        terminal
    ) and "INVENTED_" not in json.dumps(terminal)
    if status == "degraded":
        report = terminal["result"]["report"]
        assert report["status"] == "degraded"
        if failure in {"missing", "quote", "id", "rubric"}:
            assert report["coverage"]["classified_message_ids"] == [3]
            assert report["coverage"]["missing_message_ids"] == [1]
            assert report["distribution"] == [
                dict(name="Explore", count=1, percentage=100.0)
            ]
        if failure == "off_classifications":
            assert (
                report["message_classifications"]
                == report["distribution"]
                == []
            )
    else:
        assert terminal["result"] is None and terminal["coverage"] is None
    state = (
        await client.get(f"/admin/comparisons/{terminal['comparison_run_id']}")
    ).json()
    assert state["panels"][0]["status"] == status
    async with data.factory() as db:
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.attempt_no == 1 and usage.session_id is None
        assert usage.status == (
            "completed"
            if failure == "unclassified"
            else "refused" if failure == "refused" else "failed"
        )

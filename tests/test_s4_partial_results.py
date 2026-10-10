"""Partial analysis adoption and explicit retry at the HTTP/SDK seam."""

import json
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import select
from test_analysis_invocations import (
    USAGE,
    analysis_transport,
    prepare_analysis,
    result_for,
)
from test_analysis_invocations import api as analysis_api
from test_analysis_invocations import connection_api as provider_api
from test_analysis_runs import terminal
from test_student_probe import response_body

from src.models import ApiUsageLog

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_teacher_retries_partial_as_new_request_and_preserves_it_on_failure(
    data, api, monkeypatch
):
    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        if len(calls) == 1:
            value["message_classifications"][0][
                "quote"
            ] = "PRIVATE INVALID QUOTE"
        elif len(calls) == 2:
            return httpx2.Response(
                200, json=response_body("PRIVATE RAW ERROR", USAGE)
            )
        else:
            value["brief_feedback"] = ["Recovered report"]
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    view = f"/sessions/{data.session.id}/analysis"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    first_body = {"request_id": str(uuid4())}
    first = await api.post(url, json=first_body, headers=headers)
    assert first.status_code == 202
    partial = await terminal(api, first.json()["actions"]["status"])
    accepted = partial["accepted_report"]
    assert accepted["status"] == partial["latest_run"]["status"] == "degraded"
    assert partial["latest_run"]["adopted"] is True
    assert partial["latest_run"]["error_code"] == "invalid_reference"
    assert partial["retryable"] is True
    assert partial["permissions"]["can_retry"] is True
    assert "PRIVATE INVALID QUOTE" not in json.dumps(partial)
    async with data.factory() as db:
        attempt = await db.scalar(select(ApiUsageLog))
        assert (
            attempt.status == "failed"
            and attempt.error_code == "invalid_reference"
        )
        assert attempt.run_id == first.json()["run_id"]
    for _ in range(2):
        assert (await api.get(view)).json()["accepted_report"] == accepted
    replay = await api.post(url, json=first_body, headers=headers)
    assert replay.json()["run_id"] == first.json()["run_id"] and len(calls) == 1

    retry = await api.post(
        url, json={"request_id": str(uuid4())}, headers=headers
    )
    assert retry.status_code == 202
    assert retry.json()["run_id"] != first.json()["run_id"]
    assert retry.json()["permissions"]["can_retry"] is False
    failed = await terminal(api, retry.json()["actions"]["status"])
    assert failed["latest_run"]["status"] == "failed"
    assert failed["latest_run"]["adopted"] is False
    assert failed["latest_run"]["preserved"] is True
    assert failed["accepted_report"] == accepted
    assert failed["permissions"]["can_retry"] is True
    assert "PRIVATE RAW ERROR" not in json.dumps(failed)

    recovery = await api.post(
        url, json={"request_id": str(uuid4())}, headers=headers
    )
    assert recovery.status_code == 202
    recovered = await terminal(api, recovery.json()["actions"]["status"])
    assert recovered["accepted_report"]["status"] == "ok"
    assert recovered["accepted_report"]["brief_feedback"] == [
        "Recovered report"
    ]
    assert recovered["latest_run"]["adopted"] is True
    assert recovered["distribution"] == {"A": 1, "B": 0}
    assert recovered["questions"][0]["label"] == "A"
    assert recovered["permissions"]["can_retry"] is False
    assert (await api.get(view)).json()["accepted_report"] == recovered[
        "accepted_report"
    ]
    cached = await api.post(
        url, json={"request_id": str(uuid4())}, headers=headers
    )
    assert cached.status_code == 200 and len(calls) == 3
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize("zero", [False, True])
async def test_partial_coverage_and_distribution_use_only_valid_classifications(
    data, api, monkeypatch, zero
):
    from src.models import Message

    await prepare_analysis(data, monkeypatch)
    data.db.add_all(
        [
            Message(session_id=data.session.id, role="teacher", content=text)
            for text in ("Hello", "Unknown", "Invalid", "Omitted")
        ]
    )
    data.db.add(
        Message(session_id=data.session.id, role="student", content="Answer")
    )
    await data.db.commit()

    async def upstream(request, body):
        value = result_for(body)
        items = value["message_classifications"]
        if zero:
            items[0].update(disposition="unclassified", rubric_id=None)
        items[1].update(disposition="non_analyzable", rubric_id=None)
        items[2].update(disposition="unclassified", rubric_id=None)
        items[3]["quote"] = "Not an exact quotation"
        items.pop()
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    result = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert result.status_code == 200
    projected = result.json()
    accepted = projected["accepted_report"]
    coverage = accepted["coverage"]
    teacher_ids = [
        m["id"] for m in projected["messages"] if m["role"] == "teacher"
    ]
    assert len(teacher_ids) == 5
    assert (
        len(coverage["input_message_ids"])
        == len(coverage["reviewed_message_ids"])
        == 6
    )
    assert coverage["classified_message_ids"] == (
        [] if zero else teacher_ids[:1]
    )
    assert coverage["non_analyzable_message_ids"] == teacher_ids[1:2]
    assert coverage["unclassified_message_ids"] == (
        [teacher_ids[0], teacher_ids[2]] if zero else teacher_ids[2:3]
    )
    assert coverage["missing_message_ids"] == teacher_ids[3:5]
    assert coverage["invalid_message_ids"] == teacher_ids[3:4]
    assert coverage["response_missing_message_ids"] == teacher_ids[:4]
    assert sum(item["count"] for item in accepted["distribution"]) == (
        0 if zero else 1
    )
    assert accepted["distribution"][0] == dict(
        name="A",
        count=0 if zero else 1,
        percentage=None if zero else 100.0,
    )
    if zero:
        assert all(
            item["percentage"] is None for item in accepted["distribution"]
        )
    assert len(accepted["message_classifications"]) == 3
    assert accepted["status"] == projected["latest_run"]["status"] == "degraded"
    assert projected["latest_run"]["error_code"] == "invalid_reference"
    assert len(calls) == 1


@pytest.mark.parametrize("blocked", ["group", "role", "legacy"])
async def test_partial_retry_rechecks_current_authority_and_native_origin(
    data, api, monkeypatch, blocked
):
    from src.models import ModelConfig

    await prepare_analysis(data, monkeypatch)

    async def upstream(request, body):
        value = result_for(body)
        value["message_classifications"][0].update(
            disposition="unclassified", rubric_id=None
        )
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    first = await api.post(url, headers=headers)
    assert first.json()["accepted_report"]["status"] == "degraded"
    if blocked == "group":
        data.owner.group_id = None
    elif blocked == "role":
        model = await data.db.scalar(select(ModelConfig))
        model.verification_state = {"analysis": {"status": "unverified"}}
    else:
        from src.services.lesson_snapshots import canonical_hash

        data.session.snapshot_origin = "legacy_reconstructed"
        data.session.source_scenario_version = None
        data.session.config_snapshot_json = {
            **data.session.config_snapshot_json,
            "unknown_fields": ["config"],
        }
        data.session.config_hash = canonical_hash(
            data.session.config_snapshot_json
        )
    await data.db.commit()
    rejected = await api.post(
        url, json={"request_id": str(uuid4())}, headers=headers
    )
    assert (
        rejected.status_code
        == {"group": 403, "role": 400, "legacy": 409}[blocked]
    )
    readable = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    assert readable["accepted_report"]["status"] == "degraded"
    if blocked == "legacy":
        assert rejected.json()["detail"]["code"] == "legacy_read_only"
        assert readable["permissions"]["read_only"] is True
        assert readable["permissions"]["can_retry"] is False
        assert readable["retryable"] is False
        from test_scenario_api import login

        login(api, data.admin)
        await api.get("/health")
        rejected = await api.post(
            f"/admin/sessions/{data.session.id}/analyze_regenerate",
            json={"request_id": str(uuid4())},
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "legacy_read_only"
        readable = await api.get(f"/admin/sessions/{data.session.id}/analysis")
        assert readable.status_code == 200
        assert readable.json()["permissions"]["can_regenerate"] is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    "original,outcome",
    [
        ("ok", "degraded"),
        ("legacy", "degraded"),
        ("degraded", "degraded"),
        ("degraded", "cancelled"),
        ("degraded", "interrupted"),
        ("degraded", "storage_failed"),
    ],
)
async def test_partial_adoption_preserves_usable_reports_and_finalizes_run(
    data, api, monkeypatch, original, outcome
):
    import asyncio

    from sqlalchemy import text
    from test_scenario_api import login

    from src.models import SessionSummary
    from src.services.generation_lifecycle import interrupt_orphans

    await prepare_analysis(data, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    initial = True

    async def upstream(request, body):
        value = result_for(body)
        value["brief_feedback"] = [
            "Original report" if initial else "New report"
        ]
        if initial and original == "degraded":
            value["message_classifications"][0].update(
                disposition="unclassified", rubric_id=None
            )
        if not initial and outcome == "degraded":
            value["message_classifications"][0][
                "quote"
            ] = "PRIVATE INVALID QUOTE"
        if not initial and outcome in {"cancelled", "interrupted"}:
            entered.set()
            await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    if original == "legacy":
        data.db.add(
            SessionSummary(
                session_id=data.session.id,
                distribution_json='{"A":1}',
                feedback="Original report",
            )
        )
        await data.db.commit()
    else:
        first = await api.post(url, headers=headers)
        assert first.json()["accepted_report"]["status"] == original
    initial = False
    if original != "degraded":
        login(api, data.admin)
        await api.get("/health")
        headers = {"x-csrf-token": api.cookies["csrftoken"]}
        url = f"/admin/sessions/{data.session.id}/analyze_regenerate"
    if outcome == "storage_failed":
        async with data.factory() as db:
            await db.execute(
                text(
                    "CREATE TRIGGER reject_report BEFORE INSERT ON session_feedback_report BEGIN SELECT RAISE(ABORT,'PRIVATE STORAGE ERROR'); END"
                )
            )
            await db.commit()
    body = {"request_id": str(uuid4())}
    reserved = await api.post(url, json=body, headers=headers)
    assert reserved.status_code == 202
    previous = reserved.json()["accepted_report"]
    previous_distribution = reserved.json()["distribution"]
    previous_questions = reserved.json()["questions"]
    assert previous["brief_feedback"] == ["Original report"]
    try:
        if outcome in {"cancelled", "interrupted"}:
            await asyncio.wait_for(entered.wait(), 5)
            if outcome == "cancelled":
                assert (
                    await api.post(
                        reserved.json()["actions"]["cancel"], headers=headers
                    )
                ).status_code == 202
            else:
                await interrupt_orphans(data.factory)
    finally:
        release.set()
    result = await terminal(api, reserved.json()["actions"]["status"])
    assert result["latest_run"]["status"] == (
        "failed" if outcome == "storage_failed" else outcome
    )
    replaced = original == outcome == "degraded"
    assert result["latest_run"]["adopted"] is replaced
    assert result["latest_run"]["preserved"] is not replaced
    if replaced:
        assert result["accepted_report"]["brief_feedback"] == ["New report"]
        assert result["accepted_report"]["run_id"] == reserved.json()["run_id"]
    else:
        assert result["accepted_report"] == previous
        assert result["distribution"] == previous_distribution
        assert result["questions"] == previous_questions
    assert "PRIVATE" not in json.dumps(result)
    count = len(calls)
    replay = await api.post(url, json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["run_id"] == reserved.json()["run_id"]
    assert replay.json()["accepted_report"] == result["accepted_report"]
    assert len(calls) == count
    from src.services.analysis_runs import active_analyses

    await asyncio.gather(*list(active_analyses.values()))
    assert all(client.is_closed() for client in clients)

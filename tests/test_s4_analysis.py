"""S4 single analysis through authenticated HTTP, SQLite and SDK mocks."""

import json

import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply
from sqlalchemy import select
from test_analysis_invocations import USAGE, analysis_transport
from test_native_analysis import api as analysis_api
from test_native_analysis import connection_api as provider_api
from test_native_analysis import native_analysis
from test_student_probe import response_body

from src.models import ApiUsageLog, Message, SessionFeedbackReport

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_single_analysis_saves_v2_evidence_and_server_statistics(
    data, api, monkeypatch
):
    await native_analysis(data, monkeypatch)
    messages = [
        dict(id=m.id, role=m.role, content=m.content)
        for m in await data.db.scalars(select(Message).order_by(Message.id))
    ]
    value = analysis_reply(messages)
    value["strengths"] = [
        dict(
            message_id=messages[0]["id"],
            quote="question {x}",
            reason="원문 근거",
        )
    ]

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    result = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert (
        result.status_code == 200 and result.json()["feedback_status"] == "ok"
    )
    assert len(calls) == 1 and all(c.is_closed() for c in clients)
    report = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    accepted = report["accepted_report"]
    assert accepted["schema_version"] == 2
    assert accepted["coverage"]["classified_message_ids"] == [
        messages[0]["id"],
        messages[2]["id"],
    ]
    assert accepted["coverage"]["response_missing_message_ids"] == [
        messages[2]["id"]
    ]
    assert accepted["distribution"][0] == dict(
        name="Explore", count=2, percentage=100.0
    )
    assert report["questions"][0]["grade"] == "우수"
    assert report["questions"][0]["confidence"] is None
    assert "PRIVATE CRITERIA" not in json.dumps(report)
    async with data.factory() as db:
        stored = await db.scalar(select(SessionFeedbackReport))
        attempts = list(await db.scalars(select(ApiUsageLog)))
        assert stored.version == 2 and stored.status == "ok"
        assert [(a.operation, a.status) for a in attempts] == [
            ("analysis_unified", "completed")
        ]


@pytest.mark.parametrize(
    "boundary", ["off", "teacher_only", "greeting_only", "no_dialogue"]
)
async def test_dialogue_boundaries_have_honest_coverage_and_call_count(
    data, api, monkeypatch, boundary
):
    from sqlalchemy import delete

    await native_analysis(data, monkeypatch, enabled=boundary != "off")
    if boundary != "off":
        await data.db.execute(delete(Message))
        if boundary != "no_dialogue":
            data.db.add(
                Message(
                    session_id=data.session.id,
                    role="teacher",
                    content=(
                        "안녕하세요" if boundary == "greeting_only" else "Why?"
                    ),
                )
            )
        await data.db.commit()
    messages = [
        dict(id=m.id, role=m.role, content=m.content)
        for m in await data.db.scalars(select(Message).order_by(Message.id))
    ]
    value = analysis_reply(messages, enabled=boundary != "off")
    if boundary == "greeting_only":
        value["message_classifications"][0].update(
            disposition="non_analyzable", rubric_id=None
        )
    if boundary in {"teacher_only", "greeting_only"}:
        value["brief_feedback"] = [
            "학생 응답이 없어 이해 변화는 관찰할 수 없습니다."
        ]

    async def upstream(request, body):
        assert (
            boundary != "no_dialogue"
        ), "Empty dialogue must never call the provider"
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    response = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["feedback_status"] == (
        "no_dialogue" if boundary == "no_dialogue" else "ok"
    )
    assert len(calls) == (0 if boundary == "no_dialogue" else 1)
    if boundary == "no_dialogue":
        assert result["latest_run"]["status"] == "ok"
        assert result["latest_run"]["outcome"]["outcome"] == "no_dialogue"
        assert result["accepted_report"] is None and result["messages"] == []
        import csv
        import io

        exported = await api.get(f"/sessions/{data.session.id}/export.csv")
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows) == 1 and rows[0]["role"] == "summary"
        assert rows[0]["analysis_schema_version"] == "2"
        assert rows[0]["analysis_status"] == "no_dialogue"
        assert (
            json.loads(rows[0]["analysis_coverage_json"])["input_message_ids"]
            == []
        )
        assert json.loads(rows[0]["misconception_findings_json"]) == []
        assert rows[0]["message_analysis_disposition"] == ""
        async with data.factory() as db:
            report = await db.scalar(select(SessionFeedbackReport))
            assert (
                json.loads(report.payload_json)["metadata"]["coverage"][
                    "input_message_ids"
                ]
                == []
            )
            assert list(await db.scalars(select(ApiUsageLog))) == []
    else:
        accepted = result["accepted_report"]
        assert accepted["misconception_findings"] == []
        assert accepted["coverage"]["reviewed_message_ids"] == [
            m["id"] for m in messages
        ]
        if boundary == "off":
            assert (
                accepted["message_classifications"] == []
                and accepted["distribution"] == []
            )
        elif boundary == "greeting_only":
            assert accepted["coverage"]["non_analyzable_message_ids"] == [
                messages[0]["id"]
            ]
            assert all(
                item["percentage"] is None for item in accepted["distribution"]
            )
        elif boundary == "teacher_only":
            assert accepted["coverage"]["response_missing_message_ids"] == [
                messages[0]["id"]
            ]


async def test_partial_reference_failure_keeps_only_valid_items_and_fails_ledger_validation(
    data, api, monkeypatch
):
    await native_analysis(data, monkeypatch)
    messages = [
        dict(id=m.id, role=m.role, content=m.content)
        for m in await data.db.scalars(select(Message).order_by(Message.id))
    ]
    value = analysis_reply(messages)
    value["message_classifications"][0]["quote"] = "Invented quotation"

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    result = (
        await api.post(
            f"/sessions/{data.session.id}/analyze",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    ).json()
    assert result["feedback_status"] == "degraded" and len(calls) == 1
    accepted = result["accepted_report"]
    assert accepted["coverage"]["invalid_message_ids"] == [messages[0]["id"]]
    assert accepted["coverage"]["missing_message_ids"] == [messages[0]["id"]]
    assert accepted["coverage"]["classified_message_ids"] == [messages[2]["id"]]
    assert accepted["distribution"][0] == dict(
        name="Explore", count=1, percentage=100.0
    )
    assert "Invented quotation" not in json.dumps(result)
    async with data.factory() as db:
        row = await db.scalar(select(ApiUsageLog))
        assert (
            row.status == "failed"
            and row.error_code == "invalid_reference"
            and row.total_tokens == 15
        )


async def test_existing_v1_report_is_readable_when_old_analysis_verification_is_stale(
    data, api, monkeypatch
):
    from test_scenario_api import login

    from src.services.analysis_results import save_analysis

    _, model = await native_analysis(data, monkeypatch)
    await save_analysis(
        data.session.id,
        (
            {"A": 2},
            [],
            dict(brief_feedback=["기존 정상 총평"]),
            "ok",
            "legacy-model",
            "legacy-hash",
            [],
        ),
        data.db,
    )
    model.verification_state = {
        **model.verification_state,
        "analysis": {
            **model.verification_state["analysis"],
            "role_contract_version": "s1-v1",
        },
    }
    await data.db.commit()

    async def upstream(request, body):
        raise AssertionError("Stale verification must not execute")

    _, calls = analysis_transport(monkeypatch, upstream)
    old = (await api.get(f"/sessions/{data.session.id}/analysis")).json()
    assert old["feedback"] == "기존 정상 총평"
    assert old["accepted_report"]["status"] == "legacy"
    assert old["accepted_report"]["coverage"] is None
    assert old["accepted_report"]["brief_feedback"] == ["기존 정상 총평"]
    page = await api.get(f"/sessions/{data.session.id}/analysis_page")
    assert "기존 정상 총평" in page.text
    assert (
        f'data-result-url="/sessions/{data.session.id}/analysis"' in page.text
    )
    login(api, data.admin)
    await api.get("/health")
    failed = await api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert failed.status_code == 400 and calls == []
    async with data.factory() as db:
        stored = await db.scalar(select(SessionFeedbackReport))
        assert stored.version == 1 and stored.status == "ok"


async def test_public_result_urls_keep_teacher_and_admin_access_boundaries(
    data, api, monkeypatch
):
    from test_analysis_invocations import result_for
    from test_scenario_api import login

    await native_analysis(data, monkeypatch)

    async def upstream(request, body):
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    result = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert result.status_code == 200 and len(calls) == 1
    page = await api.get(f"/sessions/{data.session.id}/analysis_page")
    modal = await api.get(f"/sessions/{data.session.id}/analysis_modal")
    assert (
        f'data-result-url="/sessions/{data.session.id}/analysis"' in page.text
    )
    assert (
        f'data-result-url="/sessions/{data.session.id}/analysis"' in modal.text
    )
    login(api, data.other)
    assert (
        await api.get(f"/sessions/{data.session.id}/analysis")
    ).status_code == 403
    assert (
        await api.get(f"/admin/sessions/{data.session.id}/analysis")
    ).status_code == 403
    login(api, data.admin)
    projection = await api.get(f"/admin/sessions/{data.session.id}/analysis")
    admin_modal = await api.get(
        f"/admin/sessions/{data.session.id}/analysis_modal"
    )
    assert (
        projection.status_code == 200
        and projection.json()["permissions"]["can_regenerate"]
    )
    assert (
        f'data-result-url="/admin/sessions/{data.session.id}/analysis"'
        in admin_modal.text
    )
    assert "PRIVATE CRITERIA" not in projection.text + admin_modal.text

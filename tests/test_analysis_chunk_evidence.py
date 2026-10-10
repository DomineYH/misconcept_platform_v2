"""Chunk ownership and merge evidence through the real HTTP/SDK execution seam."""

import json

import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply, prompt_inputs
from sqlalchemy import select
from test_analysis_chunks import chunk_reply, confirm
from test_analysis_invocations import USAGE, analysis_transport
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_student_probe import response_body

from src.models import ApiUsageLog

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def evidence(message):
    return dict(message_id=message["id"], quote=message["content"])


@pytest.mark.parametrize(
    "boundary",
    [
        "changed",
        "classification",
        "finding",
        "coaching",
        "strength",
        "improvement",
        "reversed",
        "outside",
    ],
)
async def test_boundary_evidence_is_owned_or_declared_overlap_for_change_only(
    data, api, monkeypatch, boundary
):
    await long_session(data, monkeypatch)

    async def upstream(request, body):
        inputs = prompt_inputs(body)
        if body["text"]["format"]["name"] == "MergeAnalysisOutput":
            value = inputs["chunk_feedback"][-1]
        else:
            value = chunk_reply(body)
            if len(calls) == 2:
                reference = [
                    m
                    for m in inputs["messages"]
                    if m["id"] in inputs["reference_message_ids"]
                ]
                student = next(
                    m
                    for m in inputs["messages"]
                    if m["role"] == "student"
                    and m["id"] in inputs["owned_message_ids"]
                )
                if boundary == "classification":
                    value["message_classifications"] += analysis_reply(
                        reference
                    )["message_classifications"]
                elif boundary == "coaching":
                    value["dialogue_coaching"] = [
                        dict(
                            **evidence(reference[0]),
                            role="teacher",
                            marker="key_clue",
                            note="참조",
                        )
                    ]
                elif boundary == "strength":
                    value["strengths"] = [
                        dict(**evidence(reference[0]), reason="참조")
                    ]
                elif boundary == "improvement":
                    value["improvements"] = [
                        dict(
                            **evidence(reference[1]),
                            missed_reason="참조",
                            alternative_question="왜?",
                            alternative_reason="탐색",
                        )
                    ]
                else:
                    refs = [evidence(reference[1]), evidence(student)]
                    if boundary == "reversed":
                        refs.reverse()
                    if boundary == "finding":
                        refs = refs[:1]
                    if boundary == "outside":
                        refs = [
                            evidence(prompt_inputs(calls[0])["messages"][1])
                        ]
                    value["misconception_findings"] = [
                        dict(
                            kind=(
                                "changed"
                                if boundary in {"changed", "reversed"}
                                else "maintained"
                            ),
                            claim="원문 관찰",
                            evidence=refs,
                        )
                    ]
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted, plan, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    report = result["accepted_report"]
    valid = boundary == "changed"
    assert report["status"] == ("ok" if valid else "degraded")
    assert len(calls) == (3 if valid else 2)
    assert len(report["message_classifications"]) == 5
    assert report["coverage"]["invalid_message_ids"] == []
    if valid:
        finding = report["misconception_findings"][0]
        assert [e["message_id"] for e in finding["evidence"]] == [
            plan["chunks"][1]["reference_message_ids"][1],
            plan["chunks"][1]["message_ids"][1],
        ]
    else:
        assert result["latest_run"]["error_code"] == "invalid_reference"
        assert report["misconception_findings"] == []
        async with data.factory() as db:
            ledger = list(
                await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
            )
        assert (
            ledger[-1].status == "failed"
            and ledger[-1].error_code == "invalid_reference"
        )


async def test_identical_observations_are_deduplicated_and_conflicting_kinds_remain(
    data, api, monkeypatch
):
    await long_session(data, monkeypatch)

    async def upstream(request, body):
        value = chunk_reply(body)
        if len(calls) == 1:
            student = prompt_inputs(body)["messages"][1]
            observation = dict(
                kind="maintained",
                claim="유지 관찰",
                evidence=[evidence(student)],
            )
            value["misconception_findings"] = [
                observation,
                dict(observation),
                dict(observation, kind="deviated", claim="설정 이탈 관찰"),
            ]
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted, _, _ = await confirm(api, data.session.id)
    result = await terminal(api, accepted["actions"]["status"])
    assert result["latest_run"]["status"] == "ok"
    findings = result["accepted_report"]["misconception_findings"]
    assert [f["kind"] for f in findings] == ["maintained", "deviated"]
    assert [
        f["kind"]
        for f in prompt_inputs(calls[-1])["chunk_feedback"][0][
            "misconception_findings"
        ]
    ] == ["maintained", "deviated"]

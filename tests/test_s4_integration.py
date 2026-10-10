"""S4 handoff regressions at HTTP, SQLite, SDK and runtime boundaries."""

import ast
import asyncio
import copy
import csv
import io
import json
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from test_analysis_chunks import chunk_reply
from test_analysis_invocations import USAGE, analysis_transport, result_for
from test_analysis_plan_api import long_session
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api
from test_analysis_runs import terminal
from test_native_analysis import native_analysis
from test_scenario_api import login
from test_student_probe import response_body

from src.models import SessionSummary
from src.services.generation_lifecycle import interrupt_orphans

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def test_s4_runtime_excludes_s2_execution_and_normalization(data):
    forbidden = {"Analyzer", "SessionSynthesizer", "RuntimeOutput"}
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in forbidden, path
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "structured":
                    assert "normalize" not in {
                        arg.arg for arg in node.args.kwonlyargs
                    }, path


@pytest.mark.parametrize(
    "entry", ["teacher", "admin", "teacher_end", "admin_end"]
)
@pytest.mark.parametrize("mode", ["single", "partial", "chunked"])
async def test_s4_start_to_reader_csv_and_replay(
    data, api, monkeypatch, entry, mode
):
    if mode == "chunked":
        await long_session(data, monkeypatch)
    else:
        await native_analysis(data, monkeypatch)
    if entry.endswith("end"):
        data.session.ended_at = None
        await data.db.commit()
    admin = entry.startswith("admin")
    login(api, data.admin if admin else data.owner)
    await api.get("/health")
    prefix = "/admin" if admin else ""
    sid = data.session.id
    url = f"{prefix}/sessions/{sid}/" + (
        "analyze_regenerate" if admin else "analyze"
    )
    headers = {"x-csrf-token": api.cookies["csrftoken"]}

    async def upstream(request, body):
        value = chunk_reply(body) if mode == "chunked" else result_for(body)
        if mode == "partial":
            value["message_classifications"][0][
                "quote"
            ] = "PRIVATE INVENTED QUOTE"
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    body = dict(request_id=str(uuid4()))
    assert (await api.post(url, json=body)).status_code == 403
    if entry.endswith("end"):
        ended = await api.post(
            f"{prefix}/sessions/{sid}/end", json=body, headers=headers
        )
        assert ended.status_code == (
            202 if admin and mode != "chunked" else 200
        )
        if admin:
            started = ended
            url = f"{prefix}/sessions/{sid}/end"
        else:
            assert ended.json()["ended"] and calls == []
            started = await api.post(url, json=body, headers=headers)
    else:
        started = await api.post(url, json=body, headers=headers)
    if mode == "chunked":
        assert started.status_code == 200 and calls == []
        assert started.json()["status"] == "plan_required"
        plan = started.json()["plan"]
        body["plan_hash"] = plan["plan_hash"]
        started = await api.post(url, json=body, headers=headers)
    assert started.status_code == 202, started.text
    reservation = started.json()
    state = await terminal(api, reservation["actions"]["status"])
    expected = "degraded" if mode == "partial" else "ok"
    assert state["latest_run"]["status"] == expected
    accepted = state["accepted_report"]
    assert accepted["schema_version"] == 2 and accepted["status"] == expected
    assert accepted["run_id"] == reservation["run_id"]
    assert len(calls) == (3 if mode == "chunked" else 1)
    coverage = accepted["coverage"]
    teacher_ids = [m["id"] for m in state["messages"] if m["role"] == "teacher"]
    assert coverage["classified_message_ids"] == (
        teacher_ids[1:] if mode == "partial" else teacher_ids
    )
    assert coverage["missing_message_ids"] == (
        teacher_ids[:1] if mode == "partial" else []
    )
    assert sum(d["count"] for d in accepted["distribution"]) == len(
        teacher_ids
    ) - (mode == "partial")
    replay = await api.post(url, json=body, headers=headers)
    assert (
        replay.status_code == 200
        and replay.json()["run_id"] == reservation["run_id"]
    )
    assert len(calls) == (3 if mode == "chunked" else 1)
    projection = await api.get(f"{prefix}/sessions/{sid}/analysis")
    assert projection.json()["accepted_report"] == accepted
    for view in (
        "analysis_modal",
        "analysis_page" if not admin else "analysis",
    ):
        assert (
            await api.get(f"{prefix}/sessions/{sid}/{view}")
        ).status_code == 200
    export_url = (
        f"/admin/sessions/{sid}/download"
        if admin
        else f"/sessions/{sid}/export.csv"
    )
    exported = await api.get(export_url)
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    summary = next(row for row in rows if row["role"] == "summary")
    assert (
        summary["analysis_schema_version"] == "2"
        and summary["analysis_status"] == expected
    )
    assert json.loads(summary["analysis_coverage_json"]) == coverage
    assert (
        json.loads(summary["misconception_findings_json"])
        == accepted["misconception_findings"]
    )
    assert all(
        row["analysis_coverage_json"] == ""
        for row in rows
        if row["role"] != "summary"
    )
    assert "PRIVATE" not in projection.text + exported.text
    login(api, data.other)
    await api.get("/health")
    for path in (
        reservation["actions"]["status"],
        f"{prefix}/sessions/{sid}/analysis",
        export_url,
    ):
        assert (await api.get(path)).status_code == 403
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize("action", ["cancel", "restart", "permission"])
async def test_s4_concurrent_regeneration_interruption_preserves_report(
    data, api, monkeypatch, action
):
    await native_analysis(data, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    holding = False

    async def upstream(request, body):
        if holding:
            entered.set()
            await release.wait()
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    first = await api.post(
        f"/sessions/{data.session.id}/analyze",
        json=dict(request_id=str(uuid4())),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    original = (await terminal(api, first.json()["actions"]["status"]))[
        "accepted_report"
    ]
    login(api, data.admin)
    await api.get("/health")
    holding = True
    url = f"/admin/sessions/{data.session.id}/analyze_regenerate"
    body = dict(request_id=str(uuid4()))
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    reservation = await api.post(url, json=body, headers=headers)
    assert reservation.status_code == 202
    run = reservation.json()
    try:
        await asyncio.wait_for(entered.wait(), 5)
        replay = await api.post(url, json=body, headers=headers)
        assert replay.json()["run_id"] == run["run_id"]
        conflict = await api.post(
            url, json=dict(request_id=str(uuid4())), headers=headers
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["detail"]["run_id"] == run["run_id"]
        )
        assert len(calls) == 2
        login(api, data.other)
        await api.get("/health")
        assert (await api.get(run["actions"]["status"])).status_code == 403
        assert (
            await api.post(
                run["actions"]["cancel"],
                headers={"x-csrf-token": api.cookies["csrftoken"]},
            )
        ).status_code == 403
        login(api, data.admin)
        await api.get("/health")
        if action == "cancel":
            assert (
                await api.post(
                    run["actions"]["cancel"],
                    headers={"x-csrf-token": api.cookies["csrftoken"]},
                )
            ).status_code == 202
        elif action == "restart":
            await interrupt_orphans(data.factory)
        else:
            data.admin.role = "teacher"
            await data.db.commit()
    finally:
        release.set()
    login(api, data.owner)
    result = await terminal(
        api, run["actions"]["status"].removeprefix("/admin")
    )
    assert (
        result["latest_run"]["status"]
        == {
            "cancel": "cancelled",
            "restart": "interrupted",
            "permission": "failed",
        }[action]
    )
    assert result["latest_run"]["adopted"] is False
    assert result["accepted_report"] == original
    exported = await api.get(f"/sessions/{data.session.id}/export.csv")
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert {row["analysis_status"] for row in rows} == {"ok"}
    assert len(calls) == 2 and all(c.is_closed() for c in clients)


async def test_s4_legacy_reader_survives_stale_roles_then_explicit_probe_allows_v2(
    data, api, monkeypatch
):
    from test_role_probes import CLASSIFICATION, SYNTHESIS
    from test_student_probe import completed

    _, model = await native_analysis(data, monkeypatch)
    data.db.add(
        SessionSummary(
            session_id=data.session.id,
            distribution_json='{"A":1}',
            feedback="Original v1 feedback",
        )
    )
    old = copy.deepcopy(model.verification_state)
    old["analysis"]["role_contract_version"] = "s1-v1"
    old["mentor"] = {**old["student"], "role_contract_version": "s3-v1"}
    for role in old.values():
        role["capability_definition_version"] = "pre-s4-v3"
    model.capability_definition_version = "pre-s4-v3"
    model.verification_state = old
    await data.db.commit()

    async def upstream(request, body):
        prompt = body["input"][0]["content"]
        if "입력 JSON\n" in prompt:
            value = result_for(body)
        else:
            value = (
                CLASSIFICATION
                if body["text"]["format"]["name"] == "UnifiedAnalysisOutput"
                else SYNTHESIS
            )
        return httpx2.Response(
            200, json=response_body(json.dumps(value), USAGE)
        )

    clients, calls = analysis_transport(monkeypatch, upstream)
    legacy = (await api.get(f"/sessions/{data.session.id}/analysis")).json()[
        "accepted_report"
    ]
    assert legacy["status"] == "legacy" and legacy["coverage"] is None
    login(api, data.admin)
    await api.get("/health")
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert {s["status"] for s in state["verification_state"].values()} == {
        "stale"
    }
    url = f"/admin/sessions/{data.session.id}/analyze_regenerate"
    blocked = await api.post(
        url, json=dict(request_id=str(uuid4())), headers=headers
    )
    assert blocked.status_code == 400 and calls == []
    probe_id = str(uuid4())
    probe = await api.post(
        f"/admin/ai/models/{model.id}/probes",
        json=dict(
            expected_version=state["config_version"],
            role="analysis",
            request_id=probe_id,
        ),
        headers=headers,
    )
    assert probe.status_code == 202, probe.text
    assert (await completed(api, probe_id))["status"] == "succeeded"
    assert len(calls) == 2
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert (
        state["verification_state"]["analysis"]["role_contract_version"]
        == "s4-v2"
    )
    assert state["verification_state"]["analysis"]["status"] == "succeeded"
    assert state["verification_state"]["student"]["status"] == "stale"
    assert state["verification_state"]["mentor"]["status"] == "stale"
    assert (
        await api.get(f"/admin/sessions/{data.session.id}/analysis")
    ).json()["accepted_report"] == legacy
    started = await api.post(
        url, json=dict(request_id=str(uuid4())), headers=headers
    )
    assert started.status_code == 202, started.text
    v2 = await terminal(api, started.json()["actions"]["status"])
    assert (
        v2["accepted_report"]["status"] == "ok"
        and v2["accepted_report"]["schema_version"] == 2
    )
    assert len(calls) == 3 and all(c.is_closed() for c in clients)
    login(api, data.owner)
    exported = await api.get(f"/sessions/{data.session.id}/export.csv")
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert {row["analysis_schema_version"] for row in rows} == {"2"}
    assert "PRIVATE" not in exported.text

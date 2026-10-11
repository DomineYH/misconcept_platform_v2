"""Actual plan/confirm requests through HTTP, SQLite and a mock executor."""

import copy
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_analysis_invocations import prepare_analysis
from test_analysis_runs import api as analysis_api
from test_analysis_runs import connection_api as provider_api

from src.models import ApiUsageLog, GenerationRun, Message
from src.services.lesson_snapshots import canonical_hash

api = analysis_api
connection_api = provider_api

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def long_session(data, monkeypatch):
    await prepare_analysis(data, monkeypatch)
    await data.db.execute(delete(Message))
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"]["resolved_model_config"]["options"] = dict(
        max_output_tokens=4000, reasoning=dict(effort="medium")
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    for i in range(1, 10):
        data.db.add(
            Message(
                session_id=data.session.id,
                role="teacher" if i % 2 else "student",
                content=f"발화 {i}",
                turn_id=f"turn-{(i-1)//2}",
                turn_index=(i + 1) // 2,
            )
        )
    await data.db.commit()


async def test_plan_is_readable_without_reserving_or_calling_before_confirmation(
    data, api, monkeypatch
):
    await long_session(data, monkeypatch)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    response = await api.post(
        url, json=dict(request_id=str(uuid4())), headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "plan_required"
    plan = response.json()["plan"]
    assert plan["status"] == "chunked"
    assert plan["generation_calls"] == 3
    assert "model_options" not in plan and "PRIVATE" not in str(plan)
    read = await api.get(f"/sessions/{data.session.id}/analysis")
    assert read.status_code == 200
    assert read.json()["plan"]["plan_hash"] == plan["plan_hash"]
    async with data.factory() as db:
        assert list(await db.scalars(select(ApiUsageLog))) == []
        assert list(await db.scalars(select(GenerationRun))) == []


async def test_single_call_records_estimator_separately_from_actual_usage(
    data, api, monkeypatch
):
    import json

    import httpx2
    from test_analysis_invocations import USAGE, analysis_transport, result_for
    from test_analysis_runs import terminal
    from test_student_probe import response_body

    from src.models import SessionFeedbackReport

    await prepare_analysis(data, monkeypatch)
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"]["resolved_model_config"]["options"][
        "max_output_tokens"
    ] = 8192
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.commit()

    async def upstream(request, body):
        assert body["max_output_tokens"] == 8192
        return httpx2.Response(
            200, json=response_body(json.dumps(result_for(body)), USAGE)
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    accepted = await api.post(
        f"/sessions/{data.session.id}/analyze",
        json=dict(request_id=str(uuid4())),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert accepted.status_code == 202
    result = await terminal(api, accepted.json()["actions"]["status"])
    assert result["latest_run"]["status"] == "ok"
    async with data.factory() as db:
        run = await db.get(GenerationRun, accepted.json()["run_id"])
        plan = json.loads(run.plan_json)
        report = await db.scalar(select(SessionFeedbackReport))
        metadata = json.loads(report.payload_json)["metadata"]
        assert (
            metadata["estimator_version"]
            == plan["estimator_version"]
            == "utf8-v1-s4-20pct"
        )
        assert metadata["plan_hash"] == plan["plan_hash"]
        usage = await db.scalar(select(ApiUsageLog))
        assert usage.input_tokens == 10 and usage.output_tokens == 5
        assert usage.context_budget_json["estimator"] == "utf8-v1-s4-20pct"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changed",
    [
        "hash",
        "input",
        "options",
        "estimator",
        "limits",
        "definition",
        "permission",
    ],
)
async def test_confirmation_rechecks_current_plan_and_permission_with_zero_calls(
    data, api, monkeypatch, changed
):
    from src.services import analysis_plan, analysis_runs
    from src.services.model_capabilities import capabilities

    await long_session(data, monkeypatch)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    plan = (
        await api.post(url, json=dict(request_id=str(uuid4())), headers=headers)
    ).json()["plan"]
    calls = []

    async def executor(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Invalid confirmation cannot enter the executor")

    monkeypatch.setattr(analysis_runs, "chunk_executor", executor)
    if changed == "hash":
        plan["plan_hash"] = "wrong"
    elif changed == "input":
        message = await data.db.scalar(select(Message).order_by(Message.id))
        message.content += "바뀜"
    elif changed == "options":
        envelope = copy.deepcopy(data.session.config_snapshot_json)
        envelope["config"]["analysis"]["resolved_model_config"]["options"][
            "max_output_tokens"
        ] = 4096
        data.session.config_snapshot_json = envelope
        data.session.config_hash = canonical_hash(envelope)
    elif changed == "estimator":
        monkeypatch.setattr(analysis_plan, "ESTIMATOR_VERSION", "next")
    elif changed == "limits":
        definition = capabilities("openai", "gpt-5-mini")
        definition["combined_context_tokens"] -= 1
        monkeypatch.setattr(
            analysis_plan, "capabilities", lambda *_: definition
        )
    elif changed == "definition":
        from src.services import model_capabilities

        definition = capabilities("openai", "gpt-5-mini")
        definition["definition_version"] = "next"
        monkeypatch.setattr(
            analysis_plan, "capabilities", lambda *_: definition
        )
        monkeypatch.setattr(model_capabilities, "DEFINITION_VERSION", "next")
    else:
        data.owner.group_id = None
    await data.db.commit()
    response = await api.post(
        url,
        json=dict(request_id=str(uuid4()), plan_hash=plan["plan_hash"]),
        headers=headers,
    )
    assert response.status_code == (403 if changed == "permission" else 409)
    if changed != "permission":
        assert response.json()["detail"]["code"] == "plan_conflict"
        assert response.json()["plan"]["plan_hash"] != plan["plan_hash"]
    assert calls == []
    async with data.factory() as db:
        assert list(await db.scalars(select(ApiUsageLog))) == []
        assert list(await db.scalars(select(GenerationRun))) == []


async def test_confirmed_plan_reaches_mock_executor_once_and_is_stored(
    data, api, monkeypatch
):
    import json

    from test_analysis_runs import terminal

    from src.services import analysis_runs

    await long_session(data, monkeypatch)
    calls = []

    async def executor(*args, **kwargs):
        calls.append((args, kwargs))
        from src.services.invocation_types import InvocationError

        raise InvocationError("refused")

    monkeypatch.setattr(analysis_runs, "chunk_executor", executor)
    url = f"/sessions/{data.session.id}/analyze"
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    plan = (
        await api.post(url, json=dict(request_id=str(uuid4())), headers=headers)
    ).json()["plan"]
    body = dict(request_id=str(uuid4()), plan_hash=plan["plan_hash"])
    accepted = await api.post(url, json=body, headers=headers)
    assert accepted.status_code == 202
    result = await terminal(api, accepted.json()["actions"]["status"])
    assert result["latest_run"]["status"] == "failed"
    replay = await api.post(url, json=body, headers=headers)
    assert replay.json()["run_id"] == accepted.json()["run_id"]
    assert len(calls) == 1
    assert calls[0][1]["plan"]["plan_hash"] == plan["plan_hash"]
    async with data.factory() as db:
        run = await db.get(GenerationRun, accepted.json()["run_id"])
        stored = json.loads(run.plan_json)
        assert stored["plan_hash"] == plan["plan_hash"]
        assert [
            mid for c in stored["chunks"] for mid in c["message_ids"]
        ] == stored["message_ids"]
        assert stored["model_options"] == dict(
            max_output_tokens=4000, reasoning=dict(effort="medium")
        )
        assert list(await db.scalars(select(ApiUsageLog))) == []


@pytest.mark.parametrize("path", ["teacher", "admin"])
async def test_ending_session_waits_for_chunk_confirmation(
    data, api, monkeypatch, path
):
    from test_scenario_api import login

    await long_session(data, monkeypatch)
    data.session.ended_at = None
    await data.db.commit()
    if path == "admin":
        login(api, data.admin)
        await api.get("/health")
    headers = {"x-csrf-token": api.cookies["csrftoken"]}
    prefix = "/admin" if path == "admin" else ""
    ended = await api.post(
        f"{prefix}/sessions/{data.session.id}/end",
        json=dict(request_id=str(uuid4())),
        headers=headers,
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "plan_required"
    async with data.factory() as db:
        from src.models import Session

        assert (await db.get(Session, data.session.id)).ended_at is not None
        assert list(await db.scalars(select(ApiUsageLog))) == []
        assert list(await db.scalars(select(GenerationRun))) == []


@pytest.mark.parametrize("start", ["analyze", "end"])
@pytest.mark.parametrize("boundary", ["unit", "chunks", "merge"])
async def test_unexecutable_plans_reject_with_zero_provider_calls(
    data, api, monkeypatch, boundary, start
):
    from src.models import ModelConfig

    await long_session(data, monkeypatch)
    model = await data.db.scalar(select(ModelConfig))
    model.model_id = "gpt-5.2"
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    selection = envelope["config"]["analysis"]["resolved_model_config"]
    selection.update(
        model_id="gpt-5.2",
        options=dict(
            max_output_tokens=1700 if boundary != "merge" else 2000,
            reasoning=dict(effort="none"),
        ),
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.execute(delete(Message))
    for i in range(1, 19 if boundary == "chunks" else 10):
        data.db.add(
            Message(
                session_id=data.session.id,
                role="teacher" if boundary == "unit" or i % 2 else "student",
                content="한" * (200 if boundary == "merge" else 1),
                turn_id=f"t-{(i-1)//2}" if boundary != "unit" else f"t-{i}",
            )
        )
    await data.db.commit()
    if start == "end":
        data.session.ended_at = None
        await data.db.commit()
    result = await api.post(
        f"/sessions/{data.session.id}/{start}",
        json=dict(request_id=str(uuid4())),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert result.status_code == 422
    assert (
        result.json()["plan"]["blocked_code"]
        == {
            "unit": "unit_too_large",
            "chunks": "too_many_chunks",
            "merge": "merge_too_large",
        }[boundary]
    )
    assert result.json()["plan"]["generation_calls"] == 0
    async with data.factory() as db:
        assert list(await db.scalars(select(ApiUsageLog))) == []
        assert list(await db.scalars(select(GenerationRun))) == []

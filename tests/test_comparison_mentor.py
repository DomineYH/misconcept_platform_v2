"""Mentor comparisons at the HTTP/SSE, SQLite and mocked SDK boundary."""

import copy
import json
from types import SimpleNamespace

import httpx2
import pytest
from lesson_fixtures import LESSON_KEY, install_mentor_model, mentor_output
from sqlalchemy import func, select
from test_comparison_student import (
    classroom_counts,
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog, ComparisonRun, GenerationRun, Session

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.fixture
async def mentor_comparison(data, student, comparison):
    model = await install_mentor_model(data, student.connection)
    comparison["target_role"] = "mentor"
    comparison["current_message"] = None
    comparison["panels"][0]["model_config"].update(
        model_id=model.model_id, model_config_id=model.id
    )
    config = comparison["source_snapshot"]["config"]
    config["mentor"].update(
        mode="manual",
        name="Mentor",
        behavior_instruction="PRIVATE MENTOR",
        resolved_model_config=None,
    )
    config["mentor"]["intervention_policy"].update(
        condition="PRIVATE CONDITION", start_turn=20, min_interval_turns=20
    )
    config["runtime"]["context_turn_limit"] = 1
    comparison["canonical_context"].extend(
        [
            dict(role="teacher", content="Middle question"),
            dict(role="student", content="Middle answer"),
            dict(role="teacher", content="Target question"),
            dict(role="student", content="Target answer"),
        ]
    )
    student.responses.create.side_effect = None
    student.responses.create.return_value = SimpleNamespace(
        status="completed",
        output=[],
        output_text=mentor_output("Mentor coaching"),
        usage=None,
    )
    return comparison


async def test_manual_mentor_final_only_replay_and_classroom_isolation(
    data, client, student, mentor_comparison
):
    login(client, data.admin)
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
    before_stats = (await client.get("/admin/stats")).json()
    counters = (
        data.session.tutor_question_count,
        data.session.tutor_intervention_count,
    )
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[-1][1]["result"] == {
        "should_intervene": True,
        "coaching": "Mentor coaching",
    }
    path = f"/admin/comparisons/{events[0][1]['comparison_run_id']}"
    state = (await client.get(path)).json()
    assert (
        await client.post("/admin/comparisons", json=mentor_comparison)
    ).json() == state
    assert student.responses.create.await_count == 1
    call = student.responses.create.call_args.kwargs
    assert "stream" not in call
    assert call["input"] == [
        {
            "role": "user",
            "content": "teacher: Earlier question\nstudent: Earlier answer\nteacher: Middle question\nstudent: Middle answer\nteacher: Target question\nstudent: Target answer",
        }
    ]
    assert "PRIVATE MENTOR" in call["instructions"]
    assert "should_intervene=true" in call["instructions"]
    assert "PRIVATE ANALYSIS" not in str(call)
    assert "PRIVATE-REASON" not in response.text + str(state)
    assert (await client.get("/admin/stats")).json() == before_stats
    async with data.factory() as db:
        assert await classroom_counts(db) == before
        assert await exported(db) == before_csv
        session = await db.get(Session, data.session.id)
        assert (
            session.tutor_question_count,
            session.tutor_intervention_count,
        ) == counters
        run = await db.get(GenerationRun, events[0][1]["run_id"])
        assert run.session_id is None and run.operation == "mentor"
        assert (
            run.mentor_trigger == "manual"
            and run.mentor_reason_summary == "PRIVATE-REASON"
        )
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.usage_scope == "comparison" and usage.session_id is None
        assert usage.operation == "comparison_mentor" and usage.attempt_no == 1
        assert usage.context_budget_json["selected_prior_pairs"] == 2
        assert usage.context_budget_json["kept_prior_pairs"] == 2
        assert usage.context_budget_json["dropped_prior_pairs"] == 0
        assert usage.context_budget_json["target_pair_included"] is True


@pytest.mark.parametrize("intervene", [True, False])
async def test_auto_mentor_is_one_judgment_call_without_classroom_schedule(
    data, client, student, mentor_comparison, intervene
):
    login(client, data.admin)
    mentor_comparison["source_snapshot"]["config"]["mentor"]["mode"] = "auto"
    student.responses.create.return_value.output_text = mentor_output(
        "Auto coaching" if intervene else None
    )
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[-1][1]["result"] == dict(
        should_intervene=intervene,
        coaching="Auto coaching" if intervene else "",
    )
    assert student.responses.create.await_count == 1
    call = student.responses.create.call_args.kwargs
    assert "PRIVATE CONDITION" in call["instructions"]
    assert "PRIVATE-REASON" not in response.text
    async with data.factory() as db:
        run = await db.get(GenerationRun, events[0][1]["run_id"])
        assert run.mentor_trigger == "auto"
        assert run.result_kind == (
            "message" if intervene else "no_intervention"
        )
        assert run.mentor_reason_summary == "PRIVATE-REASON"
        session = await db.get(Session, data.session.id)
        assert (
            session.tutor_question_count
            == session.tutor_intervention_count
            == 0
        )


@pytest.mark.parametrize(
    "failure,code",
    [
        ("schema", "invalid_output"),
        ("manual_false", "invalid_output"),
        ("json", "invalid_json"),
        ("refusal", "refused"),
        ("timeout", "timeout_total"),
        ("429", "rate_limited"),
        ("storage", "storage_error"),
    ],
)
async def test_mentor_failures_never_expose_raw_output_or_retry(
    data, client, student, mentor_comparison, monkeypatch, failure, code
):
    from test_student_probe import response_body, sdk_transport

    login(client, data.admin)
    before = await classroom_counts(data.db)
    raw = mentor_output("PRIVATE RAW COACHING")
    if failure == "schema":
        raw = json.dumps({**json.loads(raw), "should_intervene": "true"})
    elif failure == "manual_false":
        raw = mentor_output(None)
    elif failure == "json":
        raw = '{"PRIVATE RAW JSON":'
    elif failure == "storage":
        from sqlalchemy import event as sql_event
        from sqlalchemy.exc import SQLAlchemyError

        def reject_result(conn, cursor, statement, parameters, context, many):
            if (
                statement.startswith("UPDATE generation_run")
                and "completed" in parameters
            ):
                raise SQLAlchemyError("PRIVATE STORAGE ERROR")

        sql_event.listen(
            data.engine.sync_engine, "before_cursor_execute", reject_result
        )

    async def upstream(request, body):
        if failure == "timeout":
            raise httpx2.ReadTimeout("PRIVATE TIMEOUT", request=request)
        if failure == "429":
            return httpx2.Response(
                429, json={"error": {"message": "PRIVATE ERROR"}}
            )
        result = response_body(raw)
        if failure == "refusal":
            result["output"][0]["content"] = [
                dict(type="refusal", refusal="PRIVATE REFUSAL")
            ]
        return httpx2.Response(200, json=result)

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, model="gpt-5.2", key=LESSON_KEY
    )
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "run.failed"]
    assert events[-1][1]["error_code"] == code
    assert events[-1][1]["result"] is None and events[-1][1]["text"] == ""
    state = (
        await client.get(
            f"/admin/comparisons/{events[0][1]['comparison_run_id']}"
        )
    ).json()
    assert (
        await client.post("/admin/comparisons", json=mentor_comparison)
    ).json() == state
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
    assert "PRIVATE RAW" not in response.text + str(state)
    assert "PRIVATE-REASON" not in response.text + str(state)
    assert (
        "PRIVATE REFUSAL" not in response.text
        and "PRIVATE ERROR" not in response.text
    )
    async with data.factory() as db:
        assert await classroom_counts(db) == before
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.attempt_no == 1 and usage.usage_scope == "comparison"
        assert usage.status == (
            "timed_out"
            if failure == "timeout"
            else (
                "refused"
                if failure == "refusal"
                else "completed" if failure == "storage" else "failed"
            )
        )
        run = await db.get(GenerationRun, events[0][1]["run_id"])
        assert run.outcome_json is None and run.partial_text is None


@pytest.mark.parametrize(
    "change,status",
    [
        ("off", 422),
        ("empty", 422),
        ("incomplete", 422),
        ("current", 422),
        ("missing_instruction", 422),
        ("missing_condition", 422),
        ("options", 422),
        ("budget", 422),
        ("unverified", 503),
        ("admission", 429),
    ],
)
async def test_mentor_all_panel_preflight_makes_no_calls_or_records(
    data, client, student, mentor_comparison, change, status
):
    from src.models import AppSetting, ModelConfig
    from src.services.call_admission import active_calls, registered_calls

    login(client, data.admin)
    mentor_comparison["panels"].append(
        {**copy.deepcopy(mentor_comparison["panels"][0]), "panel_id": "2"}
    )
    config = mentor_comparison["source_snapshot"]["config"]
    if change == "off":
        config["mentor"]["mode"] = "off"
    elif change == "empty":
        mentor_comparison["canonical_context"] = []
    elif change == "incomplete":
        mentor_comparison["canonical_context"].pop()
    elif change == "current":
        mentor_comparison["current_message"] = "Unanswered question"
    elif change == "missing_instruction":
        config["mentor"]["behavior_instruction"] = ""
    elif change == "missing_condition":
        config["mentor"].update(
            mode="auto",
            intervention_policy={
                **config["mentor"]["intervention_policy"],
                "condition": "",
            },
        )
    elif change == "options":
        mentor_comparison["panels"][1]["model_config"]["options"][
            "temperature"
        ] = 0.5
    elif change == "budget":
        mentor_comparison["canonical_context"] = [
            dict(
                role="teacher" if i % 2 == 0 else "student",
                content="길" * 50000,
            )
            for i in range(10)
        ]
    elif change == "unverified":
        model = await data.db.get(
            ModelConfig,
            mentor_comparison["panels"][0]["model_config"]["model_config_id"],
        )
        model.verification_state = {}
    elif change == "admission":
        setting = await data.db.get(AppSetting, 1)
        setting.limits_json = {**setting.limits_json, "admin": 1}
    await data.db.commit()
    response = await client.post("/admin/comparisons", json=mentor_comparison)
    assert response.status_code == status, response.text
    if status == 429:
        assert response.headers["Retry-After"] == "1"
    student.responses.create.assert_not_awaited()
    assert not active_calls and not registered_calls
    async with data.factory() as db:
        for model in (ComparisonRun, GenerationRun, ApiUsageLog):
            assert await db.scalar(select(func.count()).select_from(model)) == 0

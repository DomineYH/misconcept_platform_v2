"""Student comparisons through HTTP/SSE, SQLite and mocked provider SDK."""

import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import frames
from test_student_generation import student as student_fixture

from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    QuestionAnalysis,
    Scenario,
    Session,
    SessionFeedbackReport,
    SessionSummary,
    UiEvent,
)

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture


@pytest.fixture
async def comparison(data, student):
    config = copy.deepcopy(data.session.config_snapshot_json["config"])
    config["student"]["resolved_model_config"] = None
    config["analysis"]["resolved_model_config"] = None
    selection = dict(
        model_config_id=student.model.id,
        provider_connection_id=student.connection.id,
        provider="openai",
        model_id=student.model.model_id,
        options={"max_output_tokens": 1500, "reasoning": {"effort": "medium"}},
    )
    return dict(
        request_id=str(uuid4()),
        target_role="student",
        source_snapshot=dict(
            title="",
            subject="",
            target_grade="",
            config_schema_version=1,
            config=config,
            action="compare",
            groups=[],
            scenario_id=None,
            source_version=None,
            source_kind="unsaved_draft",
        ),
        canonical_context=[
            dict(role="teacher", content="Earlier question"),
            dict(role="student", content="Earlier answer"),
        ],
        current_message="Current question",
        panels=[dict(panel_id="1", model_config=selection)],
        parent_run_id=None,
    )


async def classroom_counts(db):
    return [
        await db.scalar(select(func.count()).select_from(model))
        for model in (
            Scenario,
            Session,
            Message,
            QuestionAnalysis,
            SessionSummary,
            SessionFeedbackReport,
            UiEvent,
        )
    ]


async def test_student_comparison_stream_replay_and_recovery_are_isolated(
    data, client, student, comparison
):
    login(client, data.admin)
    before = await classroom_counts(data.db)
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 200
    events = frames(response)
    assert [kind for kind, _ in events] == [
        "run.accepted",
        "output.delta",
        "output.delta",
        "output.completed",
    ]
    accepted = events[0][1]
    assert accepted["panel_id"] == "1" and accepted["seq"] == 0
    assert events[-1][1]["text"] == "Student answer"
    assert [value["seq"] for _, value in events] == [0, 1, 2, 3]
    path = f"/admin/comparisons/{accepted['comparison_run_id']}"
    state = (await client.get(path)).json()
    assert state["status"] == "finished"
    assert state["panels"][0]["status"] == "completed"
    assert state["panels"][0]["text"] == "Student answer"
    assert [item["id"] for item in state["canonical_context"]] == [1, 2]
    assert state["current_message_id"] == 3
    assert state["source_snapshot"]["title"] == "제목 없는 초안"
    repeat = await client.post("/admin/comparisons", json=comparison)
    assert repeat.json() == state
    assert student.responses.create.await_count == 1
    changed = await client.post(
        "/admin/comparisons", json={**comparison, "current_message": "Changed"}
    )
    assert changed.status_code == 409
    call = student.responses.create.call_args.kwargs
    assert [msg["content"] for msg in call["input"]] == [
        "Earlier question",
        "Earlier answer",
        "Current question",
    ]
    assert "PRIVATE ANALYSIS" not in str(call)
    assert "PRIVATE MENTOR" not in str(call)
    async with data.factory() as db:
        assert await classroom_counts(db) == before
        run = await db.get(GenerationRun, accepted["run_id"])
        assert run.session_id is None and run.panel_id == "1"
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.session_id is None and usage.usage_scope == "comparison"
        assert usage.attempt_no == 1 and usage.status == "completed"
        assert state["panels"][0]["usage"]["input_tokens"] == 10
        assert len(state["context_hash"]) == 64
    assert (await client.get(f"/runs/{accepted['run_id']}")).status_code == 404
    student.model.enabled = False
    await data.db.commit()
    assert (
        await client.post("/admin/comparisons", json=comparison)
    ).json() == state


async def test_comparison_running_replay_conflict_and_admin_admission(
    data, client, student, comparison
):
    import asyncio

    from src.services.call_admission import active_calls

    login(client, data.admin)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    first = asyncio.create_task(
        client.post("/admin/comparisons", json=comparison)
    )
    try:
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        state = (
            await client.post("/admin/comparisons", json=comparison)
        ).json()
        assert state["status"] == "running"
        assert any(
            permit.admin and permit.owner_id == data.admin.id
            for permit in active_calls
        )
        assert (
            await client.post(
                "/admin/comparisons",
                json={**comparison, "request_id": str(uuid4())},
            )
        ).status_code == 409
        assert (
            await client.post(
                "/admin/comparisons",
                json={**comparison, "current_message": "Changed"},
            )
        ).status_code == 409
        async with data.engine.begin() as writer:
            from sqlalchemy import text

            await writer.execute(
                text(
                    "UPDATE scenario SET title='Edit during generation' WHERE id=1"
                )
            )
    finally:
        gate.set()
        result = await first
    assert frames(result)[-1][0] == "output.completed"
    assert student.responses.create.await_count == 1
    assert not active_calls


@pytest.mark.parametrize("failure", ["provider", "ledger", "commit"])
async def test_comparison_failures_are_safe_and_never_retry(
    data, client, student, comparison, failure
):
    from sqlalchemy import event as sql_event
    from sqlalchemy.exc import SQLAlchemyError
    from test_student_generation import event

    from src.services.call_admission import active_calls

    login(client, data.admin)
    if failure == "provider":
        student.stream.events = [
            event("response.output_text.delta", delta="Provisional"),
            RuntimeError("PRIVATE_PROVIDER_ERROR"),
        ]
    elif failure == "ledger":

        def unavailable(conn, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO api_usage_log"):
                raise SQLAlchemyError("PRIVATE_LEDGER_ERROR")

        sql_event.listen(
            data.engine.sync_engine, "before_cursor_execute", unavailable
        )
    else:
        fail_once = True

        def commit_failure(conn, cursor, statement, parameters, context, many):
            nonlocal fail_once
            if (
                fail_once
                and statement.startswith("UPDATE generation_run")
                and "completed" in parameters
            ):
                fail_once = False
                raise RuntimeError("PRIVATE_STORAGE_ERROR")

        sql_event.listen(
            data.engine.sync_engine, "before_cursor_execute", commit_failure
        )
    response = await client.post("/admin/comparisons", json=comparison)
    assert response.status_code == 200
    events = frames(response)
    assert events[-1][0] == (
        "run.interrupted" if failure == "commit" else "run.failed"
    )
    assert not any(kind == "output.completed" for kind, _ in events)
    assert (
        "PRIVATE_PROVIDER_ERROR" not in response.text
        and "PRIVATE_LEDGER_ERROR" not in response.text
        and "PRIVATE_STORAGE_ERROR" not in response.text
    )
    accepted = events[0][1]
    state = (
        await client.get(f"/admin/comparisons/{accepted['comparison_run_id']}")
    ).json()
    assert state["panels"][0]["status"] == (
        "interrupted" if failure == "commit" else "failed"
    )
    if failure == "commit":
        assert state["status"] == "finished"
        assert state["panels"][0]["error_code"] == "storage_error"
    if failure == "provider":
        assert state["panels"][0]["text"] == "Provisional"
    assert student.responses.create.await_count == (
        0 if failure == "ledger" else 1
    )
    assert not active_calls


async def test_comparison_leaves_stats_and_all_classroom_csv_identical(
    data, client, student, comparison
):
    from src.services.export import CSVExporter

    login(client, data.admin)
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

    before = await exported(data.db)
    stats = (await client.get("/admin/stats")).json()
    response = await client.post("/admin/comparisons", json=comparison)
    assert frames(response)[-1][0] == "output.completed"
    async with data.factory() as db:
        assert await exported(db) == before
    assert (await client.get("/admin/stats")).json() == stats
    usage_page = await client.get("/admin/api-usage")
    assert "comparison" in usage_page.text
    assert "PRIVATE ANALYSIS" not in usage_page.text
    assert "Current question" not in usage_page.text

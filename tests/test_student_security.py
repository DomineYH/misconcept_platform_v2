"""Student request validation, authorization and replay after state changes."""

from uuid import uuid4

import pytest
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import frames
from test_student_generation import student as student_fixture

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "invalid", "content": "Question"},
        {"request_id": None, "content": "Question"},
        {"request_id": str(uuid4()), "content": ""},
        {"request_id": str(uuid4()), "content": "x" * 5001},
        {
            "request_id": str(uuid4()),
            "content": "Question",
            "turn_id": "invalid",
        },
    ],
)
async def test_invalid_student_input_is_rejected_before_generation(
    data, client, student, body
):
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream", json=body
    )
    assert response.status_code == 422
    student.responses.create.assert_not_awaited()
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 204


async def test_only_owner_can_generate_or_recover_a_run(data, client, student):
    payload = {"request_id": str(uuid4()), "content": "Owner question"}
    path = f"/sessions/{data.session.id}/turns/stream"
    for user in (data.other, data.admin):
        login(client, user)
        assert (await client.post(path, json=payload)).status_code == 403
    student.responses.create.assert_not_awaited()
    login(client, data.owner)
    response = await client.post(path, json=payload)
    run_id = frames(response)[0][1]["run_id"]
    for user in (data.other, data.admin):
        login(client, user)
        assert (await client.get(f"/runs/{run_id}")).status_code == 403
        assert (
            await client.get(
                f"/sessions/{data.session.id}/runs",
                params={"request_id": payload["request_id"]},
            )
        ).status_code == 403
    assert student.responses.create.await_count == 1


async def test_replay_survives_config_changes_and_session_end(
    data, client, student
):
    login(client, data.owner)
    payload = {"request_id": str(uuid4()), "content": "Exact question "}
    path = f"/sessions/{data.session.id}/turns/stream"
    response = await client.post(path, json=payload)
    saved = frames(response)[-1][1]["message"]
    data.scenario.chat_model = "changed-model"
    data.scenario.prompt = "Changed private instructions"
    data.scenario.is_active = 0
    await data.db.commit()
    assert (await client.post(path, json=payload)).json()["message"] == saved
    assert (
        await client.post(f"/sessions/{data.session.id}/close")
    ).status_code == 200
    assert (await client.post(path, json=payload)).json()["message"] == saved
    completed_turn = await client.post(
        path,
        json={
            **payload,
            "request_id": str(uuid4()),
            "turn_id": frames(response)[0][1]["turn_id"],
        },
    )
    assert completed_turn.status_code == 200
    assert completed_turn.json()["message"] == saved
    assert (
        await client.post(path, json={**payload, "request_id": str(uuid4())})
    ).status_code == 400
    conflict = await client.post(
        path, json={**payload, "content": "Exact question"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "request_conflict"
    assert student.responses.create.await_count == 1


async def test_group_access_and_active_scenario_checked_before_new_run(
    data, client, student
):
    login(client, data.owner)
    payload = {"request_id": str(uuid4()), "content": "Question"}
    path = f"/sessions/{data.session.id}/turns/stream"
    data.scenario.is_active = 0
    await data.db.commit()
    assert (await client.post(path, json=payload)).status_code == 404
    data.scenario.is_active = 1
    data.owner.group_id = None
    await data.db.commit()
    assert (await client.post(path, json=payload)).status_code == 403
    student.responses.create.assert_not_awaited()


async def test_student_uses_existing_settings_without_mentor_or_classifier(
    data, client, student, monkeypatch
):
    from sqlalchemy import select

    from src.config import config
    from src.models import ApiUsageLog, GenerationRun, PromptTemplate
    from src.services import base

    tutor = PromptTemplate(
        bot_type="tutor",
        template_name="Enabled mentor",
        template_text="Mentor instruction template",
    )
    data.db.add(tutor)
    await data.db.flush()
    data.scenario.tutor_template_id = tutor.id
    data.scenario.chat_model = "scenario-model"
    await data.db.commit()
    monkeypatch.setattr(config, "STUDENT_REASONING", "low")
    monkeypatch.setattr(config, "STUDENT_MAX_TOKENS", 1234)
    created = []

    def sdk(**kwargs):
        created.append(kwargs)
        return student

    monkeypatch.setattr(base, "AsyncOpenAI", sdk)
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Current question"},
    )
    assert frames(response)[-1][0] == "output.completed"
    assert created == [{"api_key": "test-only", "max_retries": 0}]
    assert student.responses.create.await_count == 1
    kwargs = student.responses.create.call_args.kwargs
    assert kwargs["model"] == "scenario-model"
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["max_output_tokens"] == 1234
    assert kwargs["input"][-1] == {
        "role": "user",
        "content": "Current question",
    }
    assert sum(m["content"] == "Current question" for m in kwargs["input"]) == 1
    assert kwargs["input"][0]["role"] == "developer"
    assert "Test misconception" in kwargs["input"][1]["content"]
    async with data.factory() as reader:
        usage = (await reader.scalars(select(ApiUsageLog))).one()
        assert (
            usage.bot_type,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
        ) == ("student", 10, 2, 12)
        run = (await reader.scalars(select(GenerationRun))).one()
        assert run.first_output_at is not None
        assert run.started_at <= run.first_output_at <= run.finished_at


async def test_recovery_returns_only_public_fields_and_saved_message(
    data, client, student
):
    login(client, data.owner)
    payload = {
        "request_id": str(uuid4()),
        "content": "Recover without accepted",
    }
    path = f"/sessions/{data.session.id}"
    response = await client.post(f"{path}/turns/stream", json=payload)
    message = frames(response)[-1][1]["message"]
    lookup = f"{path}/runs?request_id={payload['request_id']}"
    snapshot = (await client.get(lookup)).json()
    run_path = f"/runs/{snapshot['run_id']}"
    assert snapshot == (await client.get(run_path)).json()
    assert set(snapshot) == {
        "run_id",
        "request_id",
        "session_id",
        "turn_id",
        "turn_index",
        "operation",
        "status",
        "teacher_message_id",
        "result_kind",
        "message",
        "partial_text",
        "error_code",
        "retryable",
    }
    assert snapshot["message"] == message
    assert snapshot["status"] == "completed"
    assert snapshot["retryable"] is False
    assert (await client.get(f"/runs/{uuid4()}")).status_code == 404
    assert (
        await client.get(f"{path}/runs", params={"request_id": str(uuid4())})
    ).status_code == 404
    client.cookies.clear()
    for url in (lookup, run_path):
        denied = await client.get(url)
        assert denied.status_code == 401
        assert denied.json()["code"] == "AUTH_EXPIRED"
    assert student.responses.create.await_count == 1

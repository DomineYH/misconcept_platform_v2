"""Mentor execution contracts at HTTP + SQLite + fake SDK boundaries."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import frames
from test_student_generation import student as student_fixture

from src.config import config
from src.models.prompt_template import PromptTemplate

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture


@pytest.fixture
async def mentor(data, student):
    from lesson_fixtures import configure_mentor, install_mentor_model

    student.mentor_model = await install_mentor_model(data, student.connection)
    template = PromptTemplate(
        bot_type="tutor",
        template_name="Mentor",
        template_text="Coach {scenario_title}: {prompt} {student_profile}",
    )
    data.db.add(template)
    await data.db.flush()
    data.scenario.tutor_template_id = template.id
    data.scenario.tutor_sensitivity = "low"
    await data.db.commit()
    await configure_mentor(data, student)

    async def create(**kwargs):
        if kwargs.get("stream"):
            return student.stream
        return SimpleNamespace(output_text="Mentor coaching", usage=None)

    student.responses.create.side_effect = create
    return student


async def complete_turn(client, data, content="Why?"):
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": content},
    )
    assert response.status_code == 200, response.text
    return frames(response)[0][1]


def mentor_url(data, turn):
    return f"/sessions/{data.session.id}/turns/{turn['turn_id']}/mentor/stream"


async def test_no_intervention_is_durable_and_replayed(data, client, mentor):
    from lesson_fixtures import configure_mentor

    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = SimpleNamespace(
        output_text='{"is_repetitive":false,"is_inappropriate":false,"reason":"No help needed"}',
        usage=None,
    )
    payload = {"request_id": str(uuid4()), "trigger": "auto"}
    response = await client.post(mentor_url(data, turn), json=payload)
    assert response.status_code == 200, response.text
    events = frames(response)
    assert [kind for kind, _ in events] == ["run.accepted", "output.completed"]
    assert events[0][1]["operation"] == "mentor"
    assert events[-1][1]["result_kind"] == "no_intervention"
    assert events[-1][1]["message"] is None
    for request in (payload, {"request_id": str(uuid4()), "trigger": "auto"}):
        replay = await client.post(mentor_url(data, turn), json=request)
        assert replay.json()["run_id"] == events[0][1]["run_id"]
        assert replay.json()["result_kind"] == "no_intervention"
        assert replay.json()["status"] == "completed"
    state = await client.get(f"/runs/{events[0][1]['run_id']}")
    assert state.json()["result_kind"] == "no_intervention"
    assert mentor.responses.create.await_count == 2  # Student and judgment.
    from sqlalchemy import select

    from src.models import ApiUsageLog

    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.role == "mentor")
            )
        ).all()
        assert len(attempts) == 1 and attempts[0].operation == "mentor_judgment"
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 0


async def test_slow_mentor_does_not_block_student_and_busy_creates_nothing(
    data, client, mentor
):
    import asyncio

    from sqlalchemy import text

    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    first_turn = await complete_turn(client, data, "Why?")
    entered, gate = asyncio.Event(), asyncio.Event()
    feedback_inputs = []

    async def create(**kwargs):
        if kwargs.get("stream"):
            return mentor.stream
        feedback_inputs.append(kwargs)
        entered.set()
        await gate.wait()
        return SimpleNamespace(
            output_text="Coaching first turn",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
            ),
        )

    mentor.responses.create.side_effect = create
    payload = {"request_id": str(uuid4()), "trigger": "manual"}
    pending = asyncio.create_task(
        client.post(mentor_url(data, first_turn), json=payload)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second_turn = await asyncio.wait_for(
            complete_turn(client, data, "Later question must not enter coach"),
            3,
        )
        replay = await client.post(mentor_url(data, first_turn), json=payload)
        assert replay.json()["status"] == "running"
        busy_payload = {"request_id": str(uuid4()), "trigger": "manual"}
        busy = await client.post(
            mentor_url(data, second_turn), json=busy_payload
        )
        assert busy.status_code == 409
        assert busy.json()["detail"] == {
            "code": "mentor_busy",
            "run_id": replay.json()["run_id"],
        }
        missing = await client.get(
            f"/sessions/{data.session.id}/runs",
            params={"request_id": busy_payload["request_id"]},
        )
        assert missing.status_code == 404
        await data.db.refresh(data.session)
        assert data.session.tutor_question_count == 1
        assert data.session.tutor_intervention_count == 0
        async with data.engine.begin() as writer:
            await writer.execute(
                text(
                    "UPDATE scenario SET title='Edited during mentor' "
                    "WHERE id=1"
                )
            )
        assert len(feedback_inputs) == 1
        assert "Later question" not in str(feedback_inputs)
        assert feedback_inputs[0]["model"] == config.ANALYSIS_MODEL
        assert "stream" not in feedback_inputs[0]
    finally:
        gate.set()
        result = await pending
    coaching = frames(result)[-1][1]
    assert coaching["message"]["role"] == "tutor"
    assert coaching["turn_id"] == first_turn["turn_id"]
    assert coaching["turn_index"] == 1
    assert coaching["result_kind"] == "message"
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.text.count("Coaching first turn") == 1
    assert f'data-turn-id="{first_turn["turn_id"]}"' in updates.text
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 1
    assert mentor.responses.create.await_count == 3
    from sqlalchemy import select

    from src.models import ApiUsageLog, Message

    async with data.factory() as reader:
        coach = (
            await reader.scalars(select(Message).where(Message.role == "tutor"))
        ).one()
        assert coach.turn_id == first_turn["turn_id"]
        assert coach.turn_index == 1
        assert coach.generation_run_id == frames(result)[0][1]["run_id"]
        usage = (
            await reader.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).one()
        assert (usage.input_tokens, usage.output_tokens) == (10, 2)


async def test_failed_feedback_retry_counts_question_once(data, client, mentor):
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("SECRET feedback failure")
        return SimpleNamespace(output_text="Recovered coach", usage=None)

    mentor.responses.create.side_effect = create
    first = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert frames(first)[-1][0] == "run.failed"
    assert "SECRET" not in first.text
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 0
    retry = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert frames(retry)[-1][1]["message"]["content"] == "Recovered coach"
    assert frames(retry)[0][1]["run_id"] != frames(first)[0][1]["run_id"]
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 1
    assert data.session.tutor_intervention_count == 1
    for _ in range(2):
        duplicate = await client.post(
            mentor_url(data, turn),
            json={"request_id": str(uuid4()), "trigger": "manual"},
        )
        assert duplicate.json()["message"]["content"] == "Recovered coach"
    assert calls == 2
    from sqlalchemy import select

    from src.models import ApiUsageLog

    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.operation == "mentor")
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert [a.status for a in attempts] == ["failed", "completed"]
        assert [a.run_id for a in attempts] == [
            frames(first)[0][1]["run_id"],
            frames(retry)[0][1]["run_id"],
        ]
        assert all(
            a.input_tokens is None and a.attempt_no == 1 for a in attempts
        )


async def test_unconverted_chat_has_no_native_snapshot_and_is_blocked(
    data, client, mentor
):
    data.scenario.config_json = None
    data.session.config_snapshot_json = None
    await data.db.commit()
    login(client, data.owner)
    html = await client.get(f"/scenarios/{data.scenario.id}")
    assert html.status_code == 400
    assert html.json()["detail"] == {"code": "configuration_unavailable"}
    data.scenario.tutor_template_id = None
    await data.db.commit()
    html = await client.get(f"/scenarios/{data.scenario.id}")
    assert html.status_code == 400
    assert html.json()["detail"] == {"code": "configuration_unavailable"}
    mentor.responses.create.assert_not_awaited()


async def test_snapshot_mentor_off_blocks_direct_call(data, client, mentor):
    from lesson_fixtures import configure_mentor

    await configure_mentor(data, mentor, "off")
    login(client, data.owner)
    turn = await complete_turn(client, data)
    data.scenario.tutor_template_id = None
    await data.db.commit()
    mentor.responses.create.reset_mock()
    disabled = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert disabled.status_code == 400
    assert disabled.json()["detail"]["code"] == "mentor_disabled"
    mentor.responses.create.assert_not_awaited()
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 0

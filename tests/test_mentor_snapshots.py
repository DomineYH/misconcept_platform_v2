"""S2 mentor contracts through authenticated HTTP and mocked provider SDKs."""

from uuid import uuid4

import pytest
from lesson_fixtures import configure_mentor, mentor_output
from test_mentor_generation import (
    client,
    complete_turn,
    mentor,
    mentor_url,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

__all__ = ["client", "mentor", "scenario_payload", "student"]


async def request_mentor(client, data, turn, trigger="manual", request_id=None):
    return await client.post(
        mentor_url(data, turn),
        json={
            "request_id": request_id or str(uuid4()),
            "trigger": trigger,
        },
    )


async def test_manual_mentor_uses_literal_frozen_settings_once(
    data, client, mentor
):
    await configure_mentor(data, mentor)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    data.scenario.tutor_template_id = None
    data.scenario.prompt = "CURRENT PRIVATE PROMPT"
    mentor.mentor_model.default_options_json = {
        "invalid_current_defaults": True
    }
    await data.db.commit()
    mentor.responses.create.reset_mock()
    result = await request_mentor(client, data, turn)
    assert result.status_code == 200, result.text
    assert frames(result)[-1][1]["message"]["content"] == "Mentor coaching"
    assert mentor.responses.create.await_count == 1
    body = mentor.responses.create.call_args.kwargs
    assert body["model"] == "gpt-5.2"
    assert body["max_output_tokens"] == 1500
    assert body["reasoning"] == {"effort": "medium"}
    assert 'Coach {literal} {{braces}} {"json":true}' in str(body)
    assert "PRIVATE ANALYSIS" not in str(body)
    assert "PRIVATE ANSWER" not in str(body)
    assert "CURRENT PRIVATE PROMPT" not in str(body)
    duplicate = await request_mentor(client, data, turn)
    assert duplicate.json()["run_id"] == frames(result)[0][1]["run_id"]
    assert mentor.responses.create.await_count == 1


async def test_negative_auto_check_allows_manual_help_and_trigger_conflicts(
    data, client, mentor
):
    from types import SimpleNamespace

    await configure_mentor(
        data, mentor, "auto", start_turn=1, min_interval_turns=2
    )
    login(client, data.owner)
    turn = await complete_turn(client, data, "Same question")
    mentor.responses.create.reset_mock()
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output(None, "Condition not met"),
        usage=None,
    )
    request_id = str(uuid4())
    automatic = await request_mentor(client, data, turn, "auto", request_id)
    assert frames(automatic)[-1][1]["result_kind"] == "no_intervention"
    same = await request_mentor(client, data, turn, "auto", request_id)
    assert same.json()["run_id"] == frames(automatic)[0][1]["run_id"]
    conflict = await request_mentor(client, data, turn, "manual", request_id)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "request_conflict"
    assert "PRIVATE CONDITION {literal}" in str(
        mentor.responses.create.call_args.kwargs
    )
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output("Explicit help"), usage=None
    )
    manual = await request_mentor(client, data, turn)
    assert frames(manual)[-1][1]["message"]["content"] == "Explicit help"
    assert mentor.responses.create.await_count == 2
    replay = await request_mentor(client, data, turn, "auto", request_id)
    assert replay.json()["result_kind"] == "no_intervention"


@pytest.mark.parametrize(
    "mode,trigger,code",
    [
        ("off", "manual", "mentor_disabled"),
        ("off", "auto", "mentor_disabled"),
        ("manual", "auto", "mentor_auto_disabled"),
    ],
)
async def test_snapshot_mode_rejects_unavailable_help(
    data, client, mentor, mode, trigger, code
):
    await configure_mentor(data, mentor, mode)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    result = await request_mentor(client, data, turn, trigger)
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == code
    mentor.responses.create.assert_not_awaited()
    page = await client.get(f"/scenarios/{data.scenario.id}")
    if mode == "off":
        assert "Welcome snapshot" not in page.text
        assert 'id="request-mentor"' not in page.text


@pytest.mark.parametrize("trigger", [None, "off", "bogus", 1])
async def test_mentor_trigger_is_explicit_and_validated(
    data, client, mentor, trigger
):
    await configure_mentor(data, mentor)
    login(client, data.owner)
    turn = await complete_turn(client, data)
    payload = {"request_id": str(uuid4())}
    if trigger is not None:
        payload["trigger"] = trigger
    result = await client.post(mentor_url(data, turn), json=payload)
    assert result.status_code == 422


async def test_auto_policy_counts_completed_pairs_and_sliding_interventions(
    data, client, mentor
):
    from types import SimpleNamespace

    await configure_mentor(
        data,
        mentor,
        "auto",
        start_turn=2,
        min_interval_turns=2,
        window_turns=3,
        max_interventions=1,
    )
    login(client, data.owner)
    from src.models import Message

    data.db.add(
        Message(
            session_id=data.session.id,
            role="teacher",
            content="Failed question",
            turn_id=str(uuid4()),
            turn_index=1,
        )
    )
    await data.db.commit()

    async def completed_pair(index):
        turn_id = str(uuid4())
        data.db.add_all(
            [
                Message(
                    session_id=data.session.id,
                    role=role,
                    content=content,
                    turn_id=turn_id,
                    turn_index=index,
                )
                for role, content in [
                    ("teacher", "Known question"),
                    ("student", "Known answer"),
                ]
            ]
        )
        await data.db.commit()
        return {"turn_id": turn_id}

    first = await completed_pair(40)
    before = mentor.responses.create.await_count
    denied = await request_mentor(client, data, first, "auto")
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "mentor_start_turn"
    assert mentor.responses.create.await_count == before
    second = await completed_pair(41)

    async def coaching(**body):
        return SimpleNamespace(
            output_text=(mentor_output("Policy coaching", "Author condition")),
            usage=None,
        )

    mentor.responses.create.side_effect = coaching
    admitted = await request_mentor(client, data, second, "auto")
    assert frames(admitted)[-1][1]["result_kind"] == "message"
    third = await completed_pair(42)
    denied = await request_mentor(client, data, third, "auto")
    assert denied.json()["detail"]["code"] == "mentor_interval"
    denied = await request_mentor(client, data, third, "manual")
    assert denied.json()["detail"]["code"] == "mentor_limit"
    fourth = await completed_pair(43)
    denied = await request_mentor(client, data, fourth, "auto")
    assert denied.json()["detail"]["code"] == "mentor_limit"
    fifth = await completed_pair(44)
    released = await request_mentor(client, data, fifth, "auto")
    assert frames(released)[-1][1]["result_kind"] == "message"
    obsolete = await request_mentor(client, data, first)
    assert obsolete.status_code == 409
    assert obsolete.json()["detail"]["code"] == "mentor_turn_obsolete"
    # Durable replay still serves the older completed coaching.
    old = await request_mentor(client, data, second)
    assert old.json()["run_id"] == frames(admitted)[0][1]["run_id"]


async def test_mentor_context_uses_frozen_window_and_excludes_other_roles(
    data, client, mentor, monkeypatch
):
    from copy import deepcopy

    from src.config import config
    from src.models import Message
    from src.services.lesson_snapshots import canonical_hash

    envelope = deepcopy(data.session.config_snapshot_json)
    envelope["config"]["runtime"]["context_turn_limit"] = 2
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await data.db.commit()
    monkeypatch.setattr(config, "CONTEXT_WINDOW_TURNS", 100)
    login(client, data.owner)
    await complete_turn(client, data, "Outside frozen window")
    prior = await complete_turn(client, data, "Previous teacher A")
    await complete_turn(client, data, "Previous teacher B")
    latest = await complete_turn(client, data, "Current teacher once")
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role="tutor",
                content="PRIVATE MENTOR HISTORY",
                turn_id=prior["turn_id"],
                turn_index=2,
            ),
            Message(
                session_id=data.session.id,
                role="student",
                content="PRIVATE GREETING",
            ),
        ]
    )
    await data.db.commit()
    response = await request_mentor(client, data, latest)
    assert frames(response)[-1][1]["result_kind"] == "message"
    body = mentor.responses.create.call_args.kwargs
    dialogue = "\n".join(message["content"] for message in body["input"])
    assert "Outside frozen window" not in dialogue
    assert "Previous teacher A" in dialogue and "Previous teacher B" in dialogue
    assert dialogue.count("Current teacher once") == 1
    assert (
        "PRIVATE MENTOR HISTORY" not in dialogue
        and "PRIVATE GREETING" not in dialogue
    )

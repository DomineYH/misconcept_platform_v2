"""Mentor input, ownership, completion and replay validation."""

from datetime import datetime
from uuid import uuid4

import pytest
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

from src.models import Message

__all__ = ["client", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize(
    "state,status",
    [
        ("auth", 401),
        ("other", 403),
        ("admin", 403),
        ("group", 403),
        ("inactive", 404),
        ("ended", 400),
        ("missing", 404),
        ("incomplete", 409),
        ("invalid_request", 422),
        ("invalid_turn", 422),
    ],
)
async def test_mentor_rejects_invalid_or_unauthorized_target_before_sdk(
    data,
    client,
    mentor,
    state,
    status,
):
    login(client, data.owner)
    turn = await complete_turn(client, data)
    payload = {"request_id": str(uuid4()), "trigger": "manual"}
    if state == "auth":
        client.cookies.clear()
    elif state in {"other", "admin"}:
        login(client, getattr(data, state))
    elif state == "group":
        data.owner.group_id = None
    elif state == "inactive":
        data.scenario.is_active = 0
    elif state == "ended":
        data.session.ended_at = datetime.now()
    elif state == "missing":
        turn["turn_id"] = str(uuid4())
    elif state == "incomplete":
        turn["turn_id"] = str(uuid4())
        data.db.add(
            Message(
                session_id=data.session.id,
                role="teacher",
                content="Unanswered",
                turn_id=turn["turn_id"],
                turn_index=2,
            )
        )
    elif state == "invalid_request":
        payload["request_id"] = "invalid"
    elif state == "invalid_turn":
        turn["turn_id"] = "invalid"
    await data.db.commit()
    mentor.responses.create.reset_mock()
    denied = await client.post(mentor_url(data, turn), json=payload)
    assert denied.status_code == status, denied.text
    mentor.responses.create.assert_not_awaited()
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 0
    assert data.session.tutor_intervention_count == 0


async def test_mentor_replay_survives_settings_and_end_and_rejects_key_conflict(
    data,
    client,
    mentor,
):
    login(client, data.owner)
    first = await complete_turn(client, data)
    payload = {"request_id": str(uuid4()), "trigger": "manual"}
    result = await client.post(mentor_url(data, first), json=payload)
    saved = frames(result)[0][1]["run_id"]
    second = await complete_turn(client, data)
    conflict = await client.post(mentor_url(data, second), json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "request_conflict"
    collision = await client.post(
        mentor_url(data, second),
        json={"request_id": second["request_id"], "trigger": "manual"},
    )
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "request_conflict"
    data.scenario.tutor_template_id = None
    data.scenario.is_active = 0
    await data.db.commit()
    await client.post(f"/sessions/{data.session.id}/close")
    for request in (payload, {"request_id": str(uuid4()), "trigger": "manual"}):
        replay = await client.post(mentor_url(data, first), json=request)
        assert replay.json()["run_id"] == saved
        assert replay.json()["status"] == "completed"
    mentor.responses.create.reset_mock()
    denied = await client.post(
        mentor_url(data, second),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert denied.status_code == 400
    mentor.responses.create.assert_not_awaited()
    for user in (data.other, data.admin):
        login(client, user)
        assert (
            await client.post(mentor_url(data, first), json=payload)
        ).status_code == 403
        assert (await client.get(f"/runs/{saved}")).status_code == 403


async def test_mentor_post_requires_signed_csrf_token(data, client, mentor):
    import httpx
    from starlette_csrf import CSRFMiddleware

    from src.main import app

    login(client, data.owner)
    turn = await complete_turn(client, data)
    login(client, data.owner)
    secured = CSRFMiddleware(
        app,
        secret="test-csrf-secret",
        header_name="x-csrf-token",
        cookie_name="csrftoken",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured),
        base_url="http://test",
        cookies=client.cookies,
    ) as browser:
        await browser.get(f"/sessions/{data.session.id}/messages/updates")
        mentor.responses.create.reset_mock()
        denied = await browser.post(
            mentor_url(data, turn),
            json={"request_id": str(uuid4()), "trigger": "manual"},
        )
        assert denied.status_code == 403
        mentor.responses.create.assert_not_awaited()
        accepted = await browser.post(
            mentor_url(data, turn),
            json={"request_id": str(uuid4()), "trigger": "manual"},
            headers={"x-csrf-token": browser.cookies.get("csrftoken")},
        )
        assert frames(accepted)[-1][1]["result_kind"] == "message"

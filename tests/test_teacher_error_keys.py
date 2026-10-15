"""Explicit error translations through authenticated teacher HTTP requests."""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from test_mentor_generation import complete_turn, mentor_url
from test_scenario_api import login
from test_teacher_language_requests import (
    client,
    mentor,
    scenario_payload,
    student,
)

from src.models import Session, UserGroup
from src.services.lesson_snapshots import canonical_hash

__all__ = ["client", "mentor", "scenario_payload", "student"]
HEADERS = {"X-UI-Language": "en"}


async def test_teacher_lookup_access_and_legacy_errors_are_english(
    data, client, student
):
    login(client, data.owner)
    client.cookies.set("ui_lang", "ko")
    sid = data.session.id
    for path, message in (
        ("/sessions/999999", "Session not found"),
        ("/scenarios/999999", "Scenario not found"),
        (f"/runs/{uuid4()}", "Run not found"),
        (f"/sessions/{sid}/runs?request_id={uuid4()}", "Run not found"),
        (f"/sessions/{sid}/analysis/runs/{uuid4()}", "Run not found"),
    ):
        response = await client.get(path, headers=HEADERS)
        assert response.status_code == 404
        assert response.json() == {"detail": message}
    response = await client.get(f"/sessions/{sid}/analysis", headers=HEADERS)
    assert response.status_code == 400
    assert response.json() == {
        "detail": "End the session before viewing analysis."
    }
    for suffix, body in (
        ("turns/stream", {"content": "Why?", "turn_id": str(uuid4())}),
        (f"turns/{uuid4()}/mentor/stream", {"trigger": "manual"}),
    ):
        response = await client.post(
            f"/sessions/{sid}/{suffix}",
            json={"request_id": str(uuid4()), **body},
            headers=HEADERS,
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Turn not found"}
    login(client, data.other)
    response = await client.get(f"/sessions/{sid}", headers=HEADERS)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    response = await client.get(
        f"/scenarios/{data.scenario.id}", headers=HEADERS
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "No group has been assigned."}
    group = UserGroup(name="Unassigned scenario group")
    data.db.add(group)
    await data.db.flush()
    data.other.group_id = group.id
    await data.db.commit()
    response = await client.get(
        f"/scenarios/{data.scenario.id}", headers=HEADERS
    )
    assert response.status_code == 403
    assert response.json() == {
        "detail": "You do not have access to this scenario."
    }
    login(client, data.owner)
    data.session.snapshot_origin = "legacy_reconstructed"
    await data.db.commit()
    response = await client.post(f"/sessions/{sid}/close", headers=HEADERS)
    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "legacy_read_only",
            "message": "Historical sessions are read only. Start a new practice with a published scenario.",
        }
    }
    data.session.ended_at = datetime(2026, 1, 2)
    await data.db.commit()
    response = await client.get(f"/sessions/{sid}/analysis", headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}
    login(client, SimpleNamespace(id=999999))
    response = await client.get("/scenarios", headers=HEADERS)
    assert response.status_code == 401
    assert response.json() == {"detail": "User not found"}


async def test_student_preflight_messages_are_english(data, client, student):
    login(client, data.owner)
    client.cookies.set("ui_lang", "ko")
    url = f"/sessions/{data.session.id}/turns/stream"

    async def send():
        return await client.post(
            url,
            json={"request_id": str(uuid4()), "content": "원본 질문"},
            headers=HEADERS,
        )

    student.model.enabled = False
    await data.db.commit()
    response = await send()
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "configuration_unavailable",
            "message": "Ask an administrator to check the AI connection and student model verification.",
        }
    }
    student.model.enabled = True
    original = deepcopy(data.session.config_snapshot_json)
    oversized = deepcopy(original)
    for field in ("internal_profile", "misconception", "behavior_instruction"):
        oversized["config"]["student"][field] = "한" * 50000
    data.session.config_snapshot_json = oversized
    data.session.config_hash = canonical_hash(oversized)
    await data.db.commit()
    response = await send()
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "context_limit",
            "message": "Shorten the question or ask an administrator to check the AI settings.",
        }
    }


async def test_mentor_preflight_message_is_english(data, client, mentor):
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.mentor_model.enabled = False
    await data.db.commit()
    response = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
        headers=HEADERS,
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "configuration_unavailable",
            "message": "Ask an administrator to check the AI connection and mentor model verification, or try again later.",
        }
    }


async def test_start_storage_error_has_safe_english_message(
    data, client, student
):
    login(client, data.owner)
    config = deepcopy(data.session.config_snapshot_json["config"])
    config["mentor"]["mode"] = "off"
    data.scenario.config_json = config
    data.scenario.config_schema_version = 1
    data.scenario.review_required = False
    state = deepcopy(student.model.verification_state)
    state["analysis"] = {**state["student"], "role_contract_version": "s4-v2"}
    student.model.verification_state = state
    await data.db.commit()

    def fail_insert(*args):
        raise SQLAlchemyError("PRIVATE DATABASE ERROR")

    event.listen(Session, "before_insert", fail_insert)
    try:
        response = await client.post(
            "/sessions", json={"scenario_id": data.scenario.id}, headers=HEADERS
        )
    finally:
        event.remove(Session, "before_insert", fail_insert)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Could not start the conversation. Please try again later."
    }
    assert "PRIVATE DATABASE ERROR" not in response.text

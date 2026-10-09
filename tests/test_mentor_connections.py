"""Mentor calls at authenticated HTTP, SQLite and official SDK transport seams."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from lesson_fixtures import LESSON_KEY
from sqlalchemy import select
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

from src.config import config
from src.models import ApiUsageLog, GenerationRun

__all__ = ["client", "mentor", "scenario_payload", "student"]


async def test_coaching_uses_db_key_exact_options_and_one_linked_attempt(
    data, client, mentor, monkeypatch
):
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    mentor.sdk_options.clear()

    async def upstream(**body):
        async with data.factory() as db:
            attempt = (
                await db.scalars(
                    select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
                )
            ).one()
            assert attempt.status == "running" and attempt.run_id
        assert body["model"] == "gpt-5.2"
        assert body["reasoning"] == {"effort": "medium"}
        assert body["max_output_tokens"] == 1500
        assert (
            'Coach {literal} {{braces}} {"json":true}' in body["instructions"]
        )
        assert len(body["input"]) == 1 and body["input"][0]["role"] == "user"
        return SimpleNamespace(output_text="DB mentor coaching", usage=None)

    mentor.responses.create.side_effect = upstream
    payload = {"request_id": str(uuid4()), "trigger": "manual"}
    response = await client.post(mentor_url(data, turn), json=payload)
    result = frames(response)[-1][1]
    assert result["message"]["content"] == "DB mentor coaching"
    replay = await client.post(mentor_url(data, turn), json=payload)
    assert replay.json()["status"] == "completed"
    assert mentor.responses.create.await_count == 1
    assert len(mentor.sdk_options) == 1
    assert mentor.sdk_options[0]["api_key"] == LESSON_KEY
    assert mentor.sdk_options[0]["max_retries"] == 0
    assert mentor.sdk_options[0]["timeout"].connect == 5
    async with data.factory() as db:
        attempt = (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.operation == "mentor")
            )
        ).one()
        assert attempt.status == "completed" and attempt.role == "mentor"
        assert attempt.run_id == replay.json()["run_id"]
        assert attempt.session_id == data.session.id
        assert attempt.request_id == payload["request_id"]
        assert attempt.attempt_no == 1 and attempt.input_tokens is None


async def test_judgment_and_coaching_share_frozen_model_and_options(
    data, client, mentor, monkeypatch
):
    from lesson_fixtures import configure_mentor

    await configure_mentor(data, mentor, "auto", start_turn=1)
    monkeypatch.setattr(
        config, "DIALOGUE_ANALYSIS_MODEL", "unregistered-hidden-model"
    )
    login(client, data.owner)
    await complete_turn(client, data, "Explain how you solved the problem")
    mentor.stream.events[-1].response.output_text = "Entirely distinct solution"
    turn = await complete_turn(client, data, "Describe another representation")
    calls = []

    async def upstream(**body):
        calls.append(body)
        if len(calls) == 1:
            assert body["model"] == "gpt-5.2"
            assert body["max_output_tokens"] == 1500
            assert body["reasoning"] == {"effort": "medium"}
            assert body["text"]["format"]["type"] == "json_schema"
            assert body["text"]["format"]["strict"] is True
            return SimpleNamespace(
                output_text='{"is_repetitive":true,"is_inappropriate":false,"reason":"Author condition"}',
                usage=None,
            )
        assert body["model"] == "gpt-5.2"
        assert "Author condition" in body["input"][0]["content"]
        return SimpleNamespace(output_text="Judgment coaching", usage=None)

    mentor.responses.create.side_effect = upstream
    payload = {"request_id": str(uuid4()), "trigger": "auto"}
    response = await client.post(mentor_url(data, turn), json=payload)
    result = frames(response)[-1][1]
    assert result["message"]["content"] == "Judgment coaching"
    assert len(calls) == 2
    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.request_id == payload["request_id"])
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert [a.operation for a in attempts] == ["mentor_judgment", "mentor"]
        assert [a.model for a in attempts] == ["gpt-5.2", "gpt-5.2"]
        assert all(
            a.status == "completed" and a.attempt_no == 1 for a in attempts
        )
        assert all(
            a.run_id == frames(response)[0][1]["run_id"] for a in attempts
        )
        assert len({a.invocation_id for a in attempts}) == 2


@pytest.mark.parametrize(
    "mode",
    [
        "disabled",
        "key_missing",
        "unverified",
        "stale",
        "model_missing",
        "invalid_options",
        "snapshot_invalid",
    ],
)
async def test_unavailable_configuration_blocks_mentor_before_acceptance(
    data, client, mentor, monkeypatch, mode, caplog
):
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    if mode == "disabled":
        mentor.connection.enabled = False
    elif mode == "key_missing":
        mentor.connection.enabled = False
        mentor.connection.encrypted_key = mentor.connection.nonce = None
        mentor.connection.masked_hint = (
            mentor.connection.encryption_key_version
        ) = None
    elif mode == "unverified":
        mentor.mentor_model.verification_state = {}
    elif mode == "stale":
        mentor.mentor_model.verification_state = {
            "mentor": {
                **mentor.mentor_model.verification_state["mentor"],
                "credential_revision": 0,
            }
        }
    elif mode == "model_missing":
        mentor.mentor_model.model_id = "unregistered-exact-model"
    elif mode == "invalid_options":
        from copy import deepcopy

        from src.services.lesson_snapshots import canonical_hash

        envelope = deepcopy(data.session.config_snapshot_json)
        envelope["config"]["mentor"]["resolved_model_config"]["options"][
            "reasoning"
        ]["effort"] = "minimal"
        data.session.config_snapshot_json = envelope
        data.session.config_hash = canonical_hash(envelope)
    else:
        data.session.config_hash = "0" * 64
    await data.db.commit()
    mentor.responses.create.reset_mock()
    response = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert response.status_code == (400 if mode == "snapshot_invalid" else 503)
    assert response.json()["detail"]["code"] == "configuration_unavailable"
    if mode != "snapshot_invalid":
        assert "관리자" in response.json()["detail"]["message"]
    assert LESSON_KEY not in response.text + caplog.text
    assert "PRIVATE_MALFORMED_TEMPLATE" not in response.text + caplog.text
    mentor.responses.create.assert_not_awaited()
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 0
    assert data.session.tutor_intervention_count == 0
    async with data.factory() as db:
        assert (
            await db.scalars(
                select(GenerationRun).where(GenerationRun.operation == "mentor")
            )
        ).all() == []
        assert (
            await db.scalars(
                select(ApiUsageLog).where(ApiUsageLog.role == "mentor")
            )
        ).all() == []


@pytest.mark.parametrize(
    "mode,code,status",
    [
        ("refusal", "refused", "refused"),
        ("output_limit", "output_limit", "failed"),
        ("transient", "transient", "failed"),
        ("empty", "empty_response", "failed"),
    ],
)
async def test_coaching_failures_record_one_attempt_without_promoting_output(
    data, client, mentor, monkeypatch, caplog, mode, code, status
):
    import httpx2
    from test_student_probe import response_body, sdk_transport

    from src.models import Message
    from src.services.call_admission import active_calls, registered_calls

    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)

    async def upstream(request, body):
        if mode == "transient":
            return httpx2.Response(
                500,
                json={
                    "error": {
                        "code": "server_error",
                        "message": "PRIVATE-UPSTREAM-ERROR",
                    }
                },
            )
        response = response_body("PRIVATE-PARTIAL-OUTPUT")
        if mode == "refusal":
            response["output"][0]["content"] = [
                {"type": "refusal", "refusal": "PRIVATE-UPSTREAM-ERROR"}
            ]
        elif mode == "output_limit":
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        else:
            response["output"] = []
        return httpx2.Response(200, json=response)

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500, key=LESSON_KEY, model="gpt-5.2"
    )
    payload = {"request_id": str(uuid4()), "trigger": "manual"}
    response = await client.post(mentor_url(data, turn), json=payload)
    assert frames(response)[-1][0] == "run.failed"
    assert frames(response)[-1][1]["code"] == code
    assert "PRIVATE-" not in response.text + caplog.text
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
    async with data.factory() as db:
        attempt = (
            await db.scalars(
                select(ApiUsageLog).where(
                    ApiUsageLog.request_id == payload["request_id"]
                )
            )
        ).one()
        assert attempt.status == status and attempt.error_code == code
        assert attempt.run_id == frames(response)[0][1]["run_id"]
        assert attempt.input_tokens is None and attempt.output_tokens is None
        assert attempt.finished_at is not None and attempt.attempt_no == 1
        assert (
            await db.scalars(select(Message).where(Message.role == "tutor"))
        ).all() == []
    await data.db.refresh(data.session)
    assert data.session.tutor_intervention_count == 0
    assert not active_calls and not registered_calls

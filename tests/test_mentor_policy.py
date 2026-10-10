"""Snapshot mentor policy replaces legacy heuristics without fallback."""

from uuid import uuid4

import httpx2 as httpx
import pytest
from lesson_fixtures import mentor_output
from openai import APIError
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

# Imported fixtures remain available to pytest in this module.
__all__ = ["client", "mentor", "scenario_payload", "student"]


@pytest.mark.parametrize(
    "sensitivity,count", [("high", 0), ("medium", 10), ("low", 100)]
)
async def test_manual_help_ignores_legacy_sensitivity_and_session_cap(
    data, client, mentor, sensitivity, count
):
    data.scenario.tutor_sensitivity = sensitivity
    data.scenario.tutor_intervention_threshold = 1
    data.session.tutor_question_count = count
    data.session.tutor_intervention_count = count
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    response = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert frames(response)[-1][1]["result_kind"] == "message"
    assert mentor.responses.create.await_count == 1
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == count + 1
    assert data.session.tutor_intervention_count == count + 1


@pytest.mark.parametrize("semantic", ["intervene", "fallback", "invalid_json"])
async def test_auto_structured_errors_fail_without_heuristic_coaching(
    data, client, mentor, semantic, caplog
):
    from types import SimpleNamespace
    from uuid import uuid4

    from lesson_fixtures import configure_mentor

    await configure_mentor(data, mentor, "auto", start_turn=1)
    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    await complete_turn(
        client, data, "Please explain how you solved the problem"
    )
    mentor.stream.events[-1].response.output_text = "Entirely distinct solution"
    turn = await complete_turn(
        client, data, "Describe another method using different representations"
    )
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if semantic == "fallback":
                raise APIError(
                    "SECRET semantic error",
                    request=httpx.Request("POST", "https://example.test"),
                    body=None,
                )
            if semantic == "invalid_json":
                return SimpleNamespace(output_text="not JSON", usage=None)
            return SimpleNamespace(
                output_text=mentor_output("Semantic coaching", "Semantic loop"),
                usage=None,
            )
        return SimpleNamespace(
            output_text=mentor_output("Semantic coaching"), usage=None
        )

    mentor.responses.create.side_effect = create
    result = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    if semantic == "intervene":
        assert frames(result)[-1][1]["result_kind"] == "message"
    else:
        assert frames(result)[-1][0] == "run.failed"
        assert frames(result)[-1][1]["code"] == (
            "invalid_json" if semantic == "invalid_json" else "invalid_output"
        )
    assert len(calls) == 1
    assert "SECRET" not in result.text + caplog.text
    assert calls[0]["max_output_tokens"] == 1500
    from sqlalchemy import select

    from src.models import ApiUsageLog

    async with data.factory() as db:
        attempts = (
            await db.scalars(
                select(ApiUsageLog)
                .where(ApiUsageLog.role == "mentor")
                .order_by(ApiUsageLog.id)
            )
        ).all()
        assert [a.operation for a in attempts] == ["mentor"]
        assert attempts[0].status == (
            "completed" if semantic == "intervene" else "failed"
        )
        if semantic == "invalid_json":
            assert attempts[0].error_code == "invalid_json"
        assert all(
            a.input_tokens is None and a.attempt_no == 1 for a in attempts
        )
        assert all(a.run_id == frames(result)[0][1]["run_id"] for a in attempts)


async def test_newer_accepted_mentor_makes_failed_old_turn_obsolete(
    data, client, mentor
):
    from types import SimpleNamespace
    from uuid import uuid4

    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    old = await complete_turn(client, data)
    old_key = {"request_id": str(uuid4()), "trigger": "manual"}
    mentor.responses.create.side_effect = RuntimeError("Failed feedback")
    failed = await client.post(mentor_url(data, old), json=old_key)
    assert frames(failed)[-1][0] == "run.failed"
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = mentor.stream
    new = await complete_turn(client, data)
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output("Newer coaching"), usage=None
    )
    completed = await client.post(
        mentor_url(data, new),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert frames(completed)[-1][0] == "output.completed"
    before = mentor.responses.create.await_count
    obsolete = await client.post(
        mentor_url(data, old),
        json={"request_id": str(uuid4()), "trigger": "manual"},
    )
    assert obsolete.status_code == 409
    assert obsolete.json()["detail"]["code"] == "mentor_turn_obsolete"
    replay = await client.post(mentor_url(data, old), json=old_key)
    assert replay.json()["status"] == "failed"
    assert replay.json()["run_id"] == frames(failed)[0][1]["run_id"]
    assert mentor.responses.create.await_count == before
    await data.db.refresh(data.session)
    assert data.session.tutor_question_count == 2
    assert data.session.tutor_intervention_count == 1


async def test_repetition_does_not_bypass_author_condition(
    data, client, mentor
):
    from types import SimpleNamespace

    from lesson_fixtures import configure_mentor

    await configure_mentor(data, mentor, "auto", start_turn=1)
    login(client, data.owner)
    await complete_turn(client, data, "Same question")
    turn = await complete_turn(client, data, "Same question")
    mentor.responses.create.reset_mock()
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output(None, "Condition not met"),
        usage=None,
    )
    result = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "auto"},
    )
    assert frames(result)[-1][1]["result_kind"] == "no_intervention"
    assert mentor.responses.create.await_count == 1
    assert "PRIVATE CONDITION" in str(mentor.responses.create.call_args.kwargs)

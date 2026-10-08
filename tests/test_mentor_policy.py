"""Legacy mentor judgment, counter, and request ordering contracts."""

from uuid import uuid4

import httpx
import pytest
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
    "sensitivity,question_count,intervention_count,threshold,kind,calls,counts",
    [
        ("high", 0, 0, 3, "message", 1, (1, 1)),
        ("medium", 0, 0, 3, "no_intervention", 0, (1, 0)),
        ("low", 0, 0, 3, "no_intervention", 0, (1, 0)),
        ("high", 4, 2, 2, "no_intervention", 0, (5, 2)),
        ("high", 10, 2, 2, "message", 1, (0, 1)),
    ],
)
async def test_sensitivity_cap_and_question_reset_keep_legacy_policy(
    data,
    client,
    mentor,
    sensitivity,
    question_count,
    intervention_count,
    threshold,
    kind,
    calls,
    counts,
):
    data.scenario.tutor_sensitivity = sensitivity
    data.scenario.tutor_intervention_threshold = threshold
    data.session.tutor_question_count = question_count
    data.session.tutor_intervention_count = intervention_count
    await data.db.commit()
    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.reset_mock()
    response = await client.post(
        mentor_url(data, turn), json={"request_id": str(uuid4())}
    )
    assert frames(response)[-1][1]["result_kind"] == kind
    assert mentor.responses.create.await_count == calls
    await data.db.refresh(data.session)
    assert (
        data.session.tutor_question_count,
        data.session.tutor_intervention_count,
    ) == counts


@pytest.mark.parametrize("semantic", ["intervene", "fallback"])
async def test_semantic_judgment_and_fallback_preserve_policy(
    data, client, mentor, semantic
):
    from types import SimpleNamespace
    from uuid import uuid4

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
            return SimpleNamespace(
                output_text='{"is_repetitive":true,"reason":"Semantic loop"}',
                usage=None,
            )
        return SimpleNamespace(output_text="Semantic coaching", usage=None)

    mentor.responses.create.side_effect = create
    result = await client.post(
        mentor_url(data, turn), json={"request_id": str(uuid4())}
    )
    expected = "message" if semantic == "intervene" else "no_intervention"
    assert frames(result)[-1][1]["result_kind"] == expected
    assert len(calls) == (2 if semantic == "intervene" else 1)
    assert "SECRET" not in result.text
    assert calls[0]["max_output_tokens"] == 200


async def test_newer_accepted_mentor_makes_failed_old_turn_obsolete(
    data, client, mentor
):
    from types import SimpleNamespace
    from uuid import uuid4

    data.scenario.tutor_sensitivity = "high"
    await data.db.commit()
    login(client, data.owner)
    old = await complete_turn(client, data)
    old_key = {"request_id": str(uuid4())}
    mentor.responses.create.side_effect = RuntimeError("Failed feedback")
    failed = await client.post(mentor_url(data, old), json=old_key)
    assert frames(failed)[-1][0] == "run.failed"
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = mentor.stream
    new = await complete_turn(client, data)
    mentor.responses.create.return_value = SimpleNamespace(
        output_text="Newer coaching", usage=None
    )
    completed = await client.post(
        mentor_url(data, new), json={"request_id": str(uuid4())}
    )
    assert frames(completed)[-1][0] == "output.completed"
    before = mentor.responses.create.await_count
    obsolete = await client.post(
        mentor_url(data, old), json={"request_id": str(uuid4())}
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


async def test_low_sensitivity_repetition_uses_jaccard_without_semantic_call(
    data,
    client,
    mentor,
):
    login(client, data.owner)
    await complete_turn(client, data, "Same question")
    turn = await complete_turn(client, data, "Same question")
    mentor.responses.create.reset_mock()
    result = await client.post(
        mentor_url(data, turn), json={"request_id": str(uuid4())}
    )
    assert frames(result)[-1][1]["result_kind"] == "message"
    assert mentor.responses.create.await_count == 1
    assert "반복적인 대화 패턴" in str(
        mentor.responses.create.call_args.kwargs["input"]
    )

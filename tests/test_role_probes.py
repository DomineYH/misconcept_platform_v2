"""Role probes at authenticated HTTP, SQLite and official SDK seams."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import text
from test_model_management import write
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import completed, prepare, response_body, sdk_transport

from src.services.call_admission import admit_call
from src.services.invocation_types import InvocationError
from src.services.model_verification import ROLE_CONTRACT_VERSIONS

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)

JUDGMENT = {"is_repetitive": False, "is_inappropriate": True, "reason": "반복"}
CLASSIFICATION = {
    "label": "A",
    "confidence": 0.9,
    "reasoning": {"summary": "탐색 질문", "improved_sentence": None},
}
SYNTHESIS = {
    "brief_feedback": ["학생의 생각을 탐색했어요."],
    "strengths": [
        {"message_id": 100, "quote": "Why?", "reason": "근거를 물었어요."}
    ],
    "improvements": [
        {
            "student_message_id": 101,
            "student_quote": "그냥 덧셈이니까요.",
            "missed_reason": "풀이 근거를 탐색해요.",
            "alternative_question": "분모가 다르면 어떻게 될까?",
            "alternative_reason": "분모의 의미를 생각해요.",
        }
    ],
    "dialogue_coaching": [
        {
            "message_id": 101,
            "role": "student",
            "marker": "key_clue",
            "note": "오개념 근거예요.",
        }
    ],
}


async def test_mentor_probe_validates_judgment_then_coaching_and_only_mentor(
    data, api, monkeypatch
):
    body = {**await prepare(api), "role": "mentor"}

    async def upstream(request, payload):
        if len(calls) == 1:
            assert payload["text"]["format"]["strict"] is True
            schema = payload["text"]["format"]["schema"]
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == {
                "is_repetitive",
                "is_inappropriate",
                "reason",
            }
            content = json.dumps(JUDGMENT)
        else:
            assert "text" not in payload and not payload.get("stream")
            content = "학생의 생각을 묻는 질문을 해보세요."
        return httpx2.Response(200, json=response_body(content))

    clients, calls = sdk_transport(monkeypatch, upstream, budget=1500)
    start = await write(api, "models/1/probes", **body)
    assert start.status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["verification_state"]["mentor"]["status"] == "succeeded"
    assert model["verification_state"]["mentor"]["verified_at"]
    assert model["verification_state"]["student"]["status"] == "unverified"
    assert model["verification_state"]["analysis"]["status"] == "unverified"
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT role,probe_step,status,attempt_no FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert rows == [
            ("mentor", "judgment", "completed", 1),
            ("mentor", "coaching", "completed", 1),
        ]


async def test_analysis_probe_validates_existing_classification_and_synthesis(
    data, api, monkeypatch
):
    body = {**await prepare(api), "role": "analysis"}

    async def upstream(request, payload):
        schema = payload["text"]["format"]
        assert schema["type"] == "json_schema" and schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        if len(calls) == 2:
            assert json.loads(payload["input"][0]["content"]) == [
                {"id": 100, "role": "teacher", "content": "Why?"},
                {"id": 101, "role": "student", "content": "그냥 덧셈이니까요."},
            ]
        return httpx2.Response(
            200,
            json=response_body(
                json.dumps(CLASSIFICATION if len(calls) == 1 else SYNTHESIS)
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream, budget=2500)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    state = (await api.get("/admin/ai/state")).json()
    assert state["probe_roles"] == ["student", "mentor", "analysis"]
    model = state["models"][0]
    assert model["probe_budgets"] == {
        "student": 1024,
        "mentor": 1500,
        "analysis": 2500,
    }
    assert model["verification_state"]["analysis"]["status"] == "succeeded"
    assert model["verification_state"]["mentor"]["status"] == "unverified"
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT role,probe_step,status,attempt_no FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert rows == [
            ("analysis", "classification", "completed", 1),
            ("analysis", "synthesis", "completed", 1),
        ]


@pytest.mark.parametrize(
    "role,step,mode,code",
    [
        ("mentor", 1, "json", "invalid_json"),
        ("mentor", 1, "type", "invalid_output"),
        ("mentor", 1, "reason_empty", "empty_response"),
        ("mentor", 1, "refused", "refused"),
        ("mentor", 2, "empty", "empty_response"),
        ("analysis", 1, "label", "invalid_reference"),
        ("analysis", 1, "confidence", "invalid_output"),
        ("analysis", 1, "low", "invalid_output"),
        ("analysis", 1, "summary_empty", "empty_response"),
        ("analysis", 2, "id", "invalid_reference"),
        ("analysis", 2, "quote", "invalid_reference"),
        ("analysis", 2, "quote_empty", "invalid_reference"),
        ("analysis", 2, "quote_whitespace", "invalid_reference"),
        ("analysis", 2, "student_quote_empty", "invalid_reference"),
        ("analysis", 2, "student_quote_whitespace", "invalid_reference"),
        ("analysis", 2, "role", "invalid_reference"),
        ("analysis", 2, "improvement_id", "invalid_reference"),
        ("analysis", 2, "coaching_role", "invalid_reference"),
        ("analysis", 2, "feedback_length", "invalid_output"),
        ("analysis", 2, "question_length", "invalid_output"),
        ("analysis", 2, "marker", "invalid_output"),
        ("analysis", 2, "empty", "empty_response"),
        ("analysis", 2, "json", "invalid_json"),
        ("analysis", 2, "limit", "output_limit"),
        ("analysis", 1, "transient", "transient"),
    ],
)
async def test_role_failure_stops_bundle_and_records_safe_error(
    data, api, monkeypatch, role, step, mode, code, caplog
):
    body = {**await prepare(api), "role": role}

    async def upstream(request, payload):
        value = deepcopy(
            JUDGMENT
            if role == "mentor"
            else (CLASSIFICATION if len(calls) == 1 else SYNTHESIS)
        )
        if len(calls) != step:
            return httpx2.Response(200, json=response_body(json.dumps(value)))
        if mode == "transient":
            return httpx2.Response(
                503,
                json={
                    "error": {
                        "code": "server_error",
                        "message": "PRIVATE-OUTPUT",
                    }
                },
            )
        if mode == "type":
            value["is_repetitive"] = "false"
        if mode == "reason_empty":
            value["reason"] = " "
        if mode == "label":
            value["label"] = "invented"
        if mode == "confidence":
            value["confidence"] = 2.0
        if mode == "low":
            value["label"] = "B"
        if mode == "summary_empty":
            value["reasoning"]["summary"] = " "
        if mode == "id":
            value["strengths"][0]["message_id"] = 999
        if mode == "quote":
            value["strengths"][0]["quote"] = "PRIVATE-OUTPUT"
        if mode in {"quote_empty", "quote_whitespace"}:
            value["strengths"][0]["quote"] = (
                "" if mode == "quote_empty" else " "
            )
        if mode in {"student_quote_empty", "student_quote_whitespace"}:
            value["improvements"][0]["student_quote"] = (
                "" if mode == "student_quote_empty" else " "
            )
        if mode == "role":
            value["strengths"][0]["message_id"] = 101
        if mode == "improvement_id":
            value["improvements"][0]["student_message_id"] = 100
        if mode == "coaching_role":
            value["dialogue_coaching"][0]["role"] = "teacher"
        if mode == "feedback_length":
            value["brief_feedback"] = ["가" * 71]
        if mode == "question_length":
            value["improvements"][0]["alternative_question"] = "가" * 61
        if mode == "marker":
            value["dialogue_coaching"][0]["marker"] = "invented"
        if mode == "empty" and role == "analysis":
            value["strengths"] = []
            value["improvements"] = []
        content = '{"PRIVATE-OUTPUT":' if mode == "json" else json.dumps(value)
        if mode == "empty" and role == "mentor":
            content = ""
        response = response_body(
            content,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        if mode == "limit":
            response.update(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        if mode == "refused":
            response["output"][0]["content"] = [
                {"type": "refusal", "refusal": "PRIVATE-OUTPUT"}
            ]
        return httpx2.Response(200, json=response)

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500 if role == "mentor" else 2500
    )
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    result = await completed(api, body["request_id"])
    assert result["status"] == "failed" and result["error_code"] == code
    state = (await api.get("/admin/ai/state")).json()
    assert state["models"][0]["verification_state"][role]["status"] == "failed"
    assert state["models"][0]["verification_state"][role]["error_code"] == code
    assert (
        "PRIVATE-OUTPUT"
        not in json.dumps(state) + json.dumps(result) + caplog.text
    )
    assert len(calls) == step and all(c.is_closed() for c in clients)
    async with data.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT status,error_code,attempt_no,total_tokens FROM api_usage_log ORDER BY id"
                )
            )
        ).all()
        assert len(rows) == step and rows[-1][:3] == (
            "refused" if code == "refused" else "failed",
            code,
            1,
        )
        if mode != "transient":
            assert rows[-1][3] == 15


@pytest.mark.parametrize("role", ["mentor", "analysis"])
async def test_only_successful_role_can_run_and_failed_retest_revokes_success(
    data, api, monkeypatch, role
):
    body = {**await prepare(api), "role": role}
    update = dict(
        expected_version=1,
        display_name="Role model",
        enabled=True,
        default_options={},
    )
    assert (await write(api, "models/1/update", **update)).status_code == 422
    failing = False

    async def upstream(request, payload):
        if failing:
            return httpx2.Response(
                200, json=response_body('{"PRIVATE-OUTPUT":')
            )
        if role == "mentor":
            content = (
                json.dumps(JUDGMENT)
                if "text" in payload
                else "질문을 해보세요."
            )
        else:
            content = json.dumps(
                CLASSIFICATION if len(calls) == 1 else SYNTHESIS
            )
        return httpx2.Response(200, json=response_body(content))

    clients, calls = sdk_transport(
        monkeypatch, upstream, budget=1500 if role == "mentor" else 2500
    )
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert (await write(api, "models/1/update", **update)).status_code == 200
    arguments = dict(
        connection_id=1,
        owner_id=data.admin.id,
        operation="classification" if role == "analysis" else "mentor",
        admin=False,
        model_config_id=1,
        expected_model_version=2,
    )
    other = "mentor" if role == "analysis" else "analysis"
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await admit_call(data.factory, role=other, **arguments)
    permit = await admit_call(data.factory, role=role, **arguments)
    permit.release()
    # Supported options are statically checked; success is not tied to config_version.
    assert (
        await write(
            api,
            "models/1/update",
            **{
                **update,
                "expected_version": 2,
                "default_options": {"max_output_tokens": 3000},
            },
        )
    ).status_code == 200
    assert (await api.get("/admin/ai/state")).json()["models"][0][
        "verification_state"
    ][role]["status"] == "succeeded"
    monkeypatch.setitem(ROLE_CONTRACT_VERSIONS, role, "s1-test-next")
    assert (await api.get("/admin/ai/state")).json()["models"][0][
        "verification_state"
    ][role]["status"] == "stale"
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await admit_call(
            data.factory,
            role=role,
            **{**arguments, "expected_model_version": 3},
        )
    monkeypatch.setitem(ROLE_CONTRACT_VERSIONS, role, "s1-v1")
    failing = True
    retest = {**body, "expected_version": 3, "request_id": str(uuid4())}
    assert (await write(api, "models/1/probes", **retest)).status_code == 202
    assert (await completed(api, retest["request_id"]))[
        "error_code"
    ] == "invalid_json"
    state = (await api.get("/admin/ai/state")).json()["models"][0]
    assert state["verification_state"][role]["status"] == "failed"
    assert (
        state["verification_state"][role]["probe_request_id"]
        == retest["request_id"]
    )
    assert state["verification_state"][other]["status"] == "unverified"
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await admit_call(
            data.factory,
            role=role,
            **{**arguments, "expected_model_version": 3},
        )
    assert (await write(api, "models/1/probes", **retest)).status_code == 202
    assert len(calls) == 3 and all(c.is_closed() for c in clients)

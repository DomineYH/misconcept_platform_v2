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

MENTOR_POSITIVE = {
    "should_intervene": True,
    "feedback": "학생의 생각을 묻는 질문을 해보세요.",
    "reason_summary": "PRIVATE-REASON",
}
MENTOR_NEGATIVE = {
    "should_intervene": False,
    "feedback": "",
    "reason_summary": "PRIVATE-REASON",
}
CLASSIFICATION = {
    "schema_version": 2,
    "message_classifications": [
        dict(
            message_id=100,
            disposition="classified",
            rubric_id="A",
            quote="Why?",
            reason="탐색 질문",
        )
    ],
    "misconception_findings": [
        dict(
            kind="maintained",
            claim="학생의 설명에서 설정된 오개념을 관찰했습니다.",
            evidence=[dict(message_id=101, quote="그냥 덧셈이니까요.")],
        )
    ],
    "brief_feedback": ["학생의 생각을 탐색했어요."],
    "strengths": [
        dict(message_id=100, quote="Why?", reason="근거를 물었어요.")
    ],
    "improvements": [
        dict(
            message_id=101,
            quote="그냥 덧셈이니까요.",
            missed_reason="풀이 근거를 탐색해요.",
            alternative_question="분모가 다르면 어떻게 될까?",
            alternative_reason="분모의 의미를 생각해요.",
        )
    ],
    "dialogue_coaching": [
        dict(
            message_id=101,
            role="student",
            marker="key_clue",
            quote="그냥 덧셈이니까요.",
            note="오개념 근거예요.",
        )
    ],
}
SYNTHESIS = {
    key: value
    for key, value in CLASSIFICATION.items()
    if key != "message_classifications"
}


async def test_capacity_definition_stales_all_roles_and_explicit_probe_refreshes_db(
    data, api, monkeypatch
):
    from src.services.model_capabilities import capabilities

    body = await prepare(api)
    definition = capabilities("openai", "gpt-5-mini")
    old_version = "openai-2026-10-09-v1"
    old_definition = {**definition, "definition_version": old_version}
    old_definition.pop("combined_context_tokens")
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE model_config SET enabled=1,capability_definition_version=:old,"
                "capabilities_json=:definition,verification_state=:states WHERE id=1"
            ),
            dict(
                old=old_version,
                definition=json.dumps(old_definition),
                states=json.dumps(
                    {
                        role: dict(
                            status="succeeded",
                            credential_revision=1,
                            connection_version=2,
                            capability_definition_version=old_version,
                            role_contract_version=contract,
                        )
                        for role, contract in ROLE_CONTRACT_VERSIONS.items()
                    }
                ),
            ),
        )
        before = (
            await db.execute(
                text(
                    "SELECT model_id,default_options_json FROM model_config WHERE id=1"
                )
            )
        ).one()

    async def upstream(request, payload):
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body())
        from test_student_probe import sse

        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.output_text.delta",
                delta="Synthetic student answer",
                sequence_number=0,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.completed",
                response=response_body(),
                sequence_number=1,
            ),
        )

    clients, calls = sdk_transport(monkeypatch, upstream)
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert {
        role: evidence["status"]
        for role, evidence in model["verification_state"].items()
    } == {role: "stale" for role in ROLE_CONTRACT_VERSIONS}
    assert model["capabilities"]["combined_context_tokens"] == 400000
    assert not calls and not clients
    update = dict(
        expected_version=1,
        display_name=model["display_name"],
        enabled=True,
        default_options={},
    )
    assert (await write(api, "models/1/update", **update)).json()["detail"][
        "code"
    ] == "role_verification_required"
    assert not calls
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    current = (await api.get("/admin/ai/state")).json()["models"][0]
    assert current["verification_state"]["student"]["status"] == "succeeded"
    assert current["verification_state"]["mentor"]["status"] == "stale"
    assert current["verification_state"]["analysis"]["status"] == "stale"
    assert ROLE_CONTRACT_VERSIONS == dict(
        student="s1-v1", mentor="s3-v1", analysis="s4-v2"
    )
    assert len(calls) == 2 and all(c.is_closed() for c in clients)
    assert (await write(api, "models/1/update", **update)).status_code == 200
    assert len(calls) == 2
    async with data.engine.connect() as db:
        stored = (
            await db.execute(
                text(
                    "SELECT capabilities_json,capability_definition_version FROM model_config WHERE id=1"
                )
            )
        ).one()
        assert json.loads(stored[0]) == definition
        assert stored[1] == definition["definition_version"]
        assert (
            await db.execute(
                text(
                    "SELECT model_id,default_options_json FROM model_config WHERE id=1"
                )
            )
        ).one() == before
        assert (
            await db.execute(
                text("SELECT context_budget_json FROM api_usage_log")
            )
        ).all() == [(None,), (None,)]


async def test_mentor_probe_validates_manual_positive_then_auto_negative_and_only_mentor(
    data, api, monkeypatch
):
    body = {**await prepare(api), "role": "mentor"}

    async def upstream(request, payload):
        assert payload["text"]["format"]["strict"] is True
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "should_intervene",
            "feedback",
            "reason_summary",
        }
        assert not payload.get("stream")
        assert ("수동" if len(calls) == 1 else "자동") in payload[
            "instructions"
        ]
        content = json.dumps(
            MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE
        )
        return httpx2.Response(200, json=response_body(content))

    clients, calls = sdk_transport(monkeypatch, upstream, budget=1500)
    start = await write(api, "models/1/probes", **body)
    assert start.status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    assert model["verification_state"]["mentor"]["status"] == "succeeded"
    assert (
        model["verification_state"]["mentor"]["role_contract_version"]
        == "s3-v1"
    )
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
            ("mentor", "manual_positive", "completed", 1),
            ("mentor", "auto_negative", "completed", 1),
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
            assert json.loads(payload["input"][0]["content"])[
                "validated_evidence"
            ] == [
                dict(message_id=100, quote="Why?"),
                dict(message_id=101, quote="그냥 덧셈이니까요."),
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
            ("analysis", "unified", "completed", 1),
            ("analysis", "merge", "completed", 1),
        ]


@pytest.mark.parametrize(
    "role,step,mode,code",
    [
        ("mentor", 1, "manual_false", "invalid_output"),
        ("mentor", 2, "auto_positive", "invalid_output"),
        ("mentor", 1, "json", "invalid_json"),
        ("mentor", 1, "type", "invalid_output"),
        ("mentor", 1, "reason_empty", "empty_response"),
        ("mentor", 1, "refused", "refused"),
        ("mentor", 2, "empty", "empty_response"),
        ("analysis", 1, "label", "invalid_reference"),
        ("analysis", 1, "confidence", "invalid_output"),
        ("analysis", 1, "low", "invalid_reference"),
        ("analysis", 1, "summary_empty", "empty_response"),
        ("analysis", 2, "id", "invalid_reference"),
        ("analysis", 2, "quote", "invalid_reference"),
        ("analysis", 2, "quote_empty", "invalid_output"),
        ("analysis", 2, "quote_whitespace", "invalid_output"),
        ("analysis", 2, "student_quote_empty", "invalid_output"),
        ("analysis", 2, "student_quote_whitespace", "invalid_output"),
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
            (MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE)
            if role == "mentor"
            else (CLASSIFICATION if len(calls) == 1 else SYNTHESIS)
        )
        if len(calls) != step:
            return httpx2.Response(200, json=response_body(json.dumps(value)))
        if mode == "manual_false":
            value = dict(MENTOR_NEGATIVE)
        if mode == "auto_positive":
            value = dict(MENTOR_POSITIVE)
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
            value["should_intervene"] = "false"
        if mode == "reason_empty":
            value["reason_summary"] = " "
        if mode == "label":
            value["message_classifications"][0]["rubric_id"] = "invented"
        if mode == "confidence":
            value["confidence"] = 2.0
        if mode == "low":
            value["message_classifications"][0][
                "disposition"
            ] = "non_analyzable"
        if mode == "summary_empty":
            value["brief_feedback"] = [" "]
        if mode == "id":
            value["strengths"][0]["message_id"] = 999
        if mode == "quote":
            value["strengths"][0]["quote"] = "PRIVATE-OUTPUT"
        if mode in {"quote_empty", "quote_whitespace"}:
            value["strengths"][0]["quote"] = (
                "" if mode == "quote_empty" else " "
            )
        if mode in {"student_quote_empty", "student_quote_whitespace"}:
            value["improvements"][0]["quote"] = (
                "" if mode == "student_quote_empty" else " "
            )
        if mode == "role":
            value["strengths"][0]["message_id"] = 101
        if mode == "improvement_id":
            value["improvements"][0]["message_id"] = 999
        if mode == "coaching_role":
            value["dialogue_coaching"][0]["role"] = "teacher"
        if mode == "feedback_length":
            value["brief_feedback"] = ["가" * 301]
        if mode == "question_length":
            value["improvements"][0]["alternative_question"] = "가" * 201
        if mode == "marker":
            value["dialogue_coaching"][0]["marker"] = "invented"
        if mode == "empty" and role == "analysis":
            value["brief_feedback"] = []
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
            content = json.dumps(
                MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE
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
    original_version = ROLE_CONTRACT_VERSIONS[role]
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
    monkeypatch.setitem(ROLE_CONTRACT_VERSIONS, role, original_version)
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

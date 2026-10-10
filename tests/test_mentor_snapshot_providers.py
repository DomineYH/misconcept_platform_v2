"""Frozen mentor roles through all three installed provider SDK transports."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import httpx2
import pytest
from lesson_fixtures import install_connection, install_snapshot
from sqlalchemy import select
from test_scenario_api import client, login
from test_student_generation import frames

from src.models import ApiUsageLog, GenerationRun, Message
from src.services import context_budget
from src.services.lesson_snapshots import canonical_hash
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key

__all__ = ["client"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
@pytest.mark.parametrize(
    "prior_pairs,trim", [(0, False), (1, False), (2, False), (2, True)]
)
@pytest.mark.parametrize(
    "trigger,positive", [("manual", True), ("auto", True), ("auto", False)]
)
async def test_mentor_single_structured_call_uses_frozen_provider_config(
    data, client, monkeypatch, provider, trigger, positive, prior_pairs, trim
):
    from test_provider_connections import KEY
    from test_student_probe import response_body, sdk_transport

    connection, model = await install_connection(data, monkeypatch)
    connection.provider = provider
    model.model_id = {
        "openai": "gpt-5-mini",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-2.5-flash",
    }[provider]
    version = capabilities(provider, model.model_id)["definition_version"]
    model.capability_definition_version = version
    evidence = dict(
        status="succeeded",
        credential_revision=1,
        connection_version=connection.connection_version,
        capability_definition_version=version,
        role_contract_version="s1-v1",
    )
    model.verification_state = {
        "student": evidence,
        "mentor": {**evidence, "role_contract_version": "s3-v1"},
    }
    connection.encrypted_key, connection.nonce = encrypt_key(connection, KEY, 1)
    options = {
        "openai": dict(max_output_tokens=1024, reasoning={"effort": "low"}),
        "anthropic": dict(
            max_output_tokens=1024,
            thinking={"type": "disabled"},
            temperature=0.4,
        ),
        "google": dict(
            max_output_tokens=1024, thinking={"budget": 0}, temperature=0.4
        ),
    }[provider]
    await install_snapshot(data, connection, model, options=options)
    envelope = deepcopy(data.session.config_snapshot_json)
    envelope["config"]["mentor"].update(
        mode="auto" if trigger == "auto" else "manual",
        name="Snapshot mentor",
        behavior_instruction="Coach {literal}",
        resolved_model_config=deepcopy(
            envelope["config"]["student"]["resolved_model_config"]
        ),
    )
    envelope["config"]["mentor"]["intervention_policy"].update(
        condition="Author condition", start_turn=1
    )
    envelope["config"]["runtime"]["context_turn_limit"] = 2
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    model.default_options_json = {"invalid_current_defaults": True}
    model.config_version += 1
    turn_id = str(uuid4())
    data.db.add_all(
        [
            Message(
                session_id=data.session.id,
                role=role,
                content=content,
                turn_id=turn_id,
                turn_index=4,
            )
            for role, content in [
                ("teacher", "Known teacher"),
                ("student", "Known student"),
            ]
        ]
    )
    for index, question in enumerate(
        [
            "OUTSIDE WINDOW",
            "Old 한🙂 {x}" * (1200 if trim else 1),
            "Recent teacher",
        ],
        1,
    ):
        if prior_pairs < 2 and index <= 3 - prior_pairs:
            continue
        prior = str(uuid4())
        data.db.add_all(
            Message(
                session_id=data.session.id,
                role=role,
                content=content,
                turn_id=prior,
                turn_index=index,
            )
            for role, content in [
                ("teacher", question),
                ("student", f"Prior answer {index}"),
            ]
        )
    data.db.add(
        Message(
            session_id=data.session.id,
            role="tutor",
            content="PRIVATE MENTOR HISTORY",
        )
    )
    await data.db.commit()
    if trim:
        definition = capabilities(provider, model.model_id)
        if provider == "google":
            definition["input_token_limit"] = 7000
        else:
            definition["combined_context_tokens"] = 8024
        monkeypatch.setattr(
            context_budget, "capabilities", lambda *_: definition
        )

    admitted_budget = None

    async def inspect_attempt():
        nonlocal admitted_budget
        async with data.factory() as db:
            attempt = (await db.scalars(select(ApiUsageLog))).one()
            admitted_budget = attempt.context_budget_json
            assert (
                admitted_budget
                and admitted_budget["target_pair_included"] is True
            )
            assert attempt.status == "running" and attempt.input_tokens is None
            assert attempt.estimated_cost_usd is None

    def content():
        return json.dumps(
            dict(
                should_intervene=positive,
                feedback="Frozen coaching" if positive else "",
                reason_summary="PRIVATE-REASON-SENTINEL",
            )
        )

    if provider == "openai":

        async def upstream(request, body):
            await inspect_attempt()
            return httpx2.Response(200, json=response_body(content()))

        clients, calls = sdk_transport(
            monkeypatch, upstream, budget=1024, key=KEY
        )
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport as claude_transport
        from test_anthropic_probes import response_body as claude_response

        async def upstream(request):
            await inspect_attempt()
            return httpx2.Response(200, json=claude_response(content()))

        clients, calls = claude_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:
        from test_google_catalog import install
        from test_google_invocations import response

        async def upstream(request):
            await inspect_attempt()
            return httpx.Response(200, json=response(content()))

        clients, calls = install(monkeypatch, upstream)
    login(client, data.owner)
    request_id = str(uuid4())
    result = await client.post(
        f"/sessions/{data.session.id}/turns/{turn_id}/mentor/stream",
        json=dict(request_id=request_id, trigger=trigger),
    )
    final = frames(result)[-1][1]
    assert final["result_kind"] == (
        "message" if positive else "no_intervention"
    ), result.text
    assert (
        final["message"]["content"] == "Frozen coaching"
        if positive
        else final["message"] is None
    )
    assert "PRIVATE-REASON-SENTINEL" not in result.text
    assert len(calls) == 1
    for call in calls:
        body = call if provider == "openai" else json.loads(call.content)
        if provider == "openai":
            assert body["model"] == "gpt-5-mini"
            assert body["max_output_tokens"] == 1024
            assert body["reasoning"] == {"effort": "low"}
            instruction = body["instructions"]
            messages = body["input"]
            schema = body["text"]["format"]["schema"]
            assert body["text"]["format"]["strict"] is True
        elif provider == "anthropic":
            assert body["model"] == "claude-sonnet-4-6"
            assert body["max_tokens"] == 1024
            assert body["thinking"] == {"type": "disabled"}
            assert body["temperature"] == 0.4
            instruction = body["system"]
            messages = body["messages"]
            schema = body["output_config"]["format"]["schema"]
        else:
            assert call.url.path.endswith("gemini-2.5-flash:generateContent")
            assert body["generationConfig"]["maxOutputTokens"] == 1024
            assert (
                body["generationConfig"]["thinkingConfig"]["thinking_budget"]
                == 0
            )
            assert body["generationConfig"]["temperature"] == 0.4
            instruction = body["systemInstruction"]["parts"][0]["text"]
            assert [item["role"] for item in body["contents"]] == ["user"]
            messages = [
                dict(role=item["role"], content=item["parts"][0]["text"])
                for item in body["contents"]
            ]
            schema = body["generationConfig"]["responseJsonSchema"]
        assert set(schema["required"]) == {
            "should_intervene",
            "feedback",
            "reason_summary",
        }
        assert schema["additionalProperties"] is False
        assert "Coach {literal}" in instruction
        assert (
            "Author condition" in instruction
            if trigger == "auto"
            else "수동 도움" in instruction
        )
        dialogue = "\n".join(item["content"] for item in messages)
        assert len(messages) == 1 and messages[0]["role"] == "user"
        expected_dialogue = "teacher: Known teacher\nstudent: Known student"
        if prior_pairs:
            expected_dialogue = (
                "teacher: Recent teacher\nstudent: Prior answer 3\n"
                + expected_dialogue
            )
        old_included = prior_pairs == 2 and not trim
        if old_included:
            expected_dialogue = (
                "teacher: Old 한🙂 {x}\nstudent: Prior answer 2\n"
                + expected_dialogue
            )
        assert messages == [dict(role="user", content=expected_dialogue)]
        assert (
            "OUTSIDE WINDOW" not in dialogue
            and "PRIVATE MENTOR HISTORY" not in dialogue
        )
        assert ("Old 한🙂" in dialogue) == old_included
        assert ("Prior answer 2" in dialogue) == old_included
        assert ("Recent teacher" in dialogue) == (prior_pairs > 0)
        assert ("Prior answer 3" in dialogue) == (prior_pairs > 0)
        assert (
            dialogue.count("Known teacher")
            == dialogue.count("Known student")
            == 1
        )
        envelope = dict(
            system_instruction=instruction,
            messages=messages,
            output_schema=schema,
        )
        actual_estimate = (
            len(
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            + 32 * len(messages)
            + 1024
        )
        assert admitted_budget["estimated_input_tokens"] == actual_estimate
        assert actual_estimate <= admitted_budget["input_budget_tokens"]
        assert (
            "PRIVATE ANALYSIS" not in instruction
            and "PRIVATE ANSWER" not in instruction
        )
    assert all(
        sdk.is_closed if provider == "google" else sdk.is_closed()
        for sdk in clients
    )
    async with data.factory() as db:
        run = (await db.scalars(select(GenerationRun))).one()
        assert (run.provider, run.model, run.config_hash) == (
            provider,
            model.model_id,
            data.session.config_hash,
        )
        assert run.mentor_reason_summary == "PRIVATE-REASON-SENTINEL"
        assert run.mentor_trigger == trigger
        attempts = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
        assert [row.operation for row in attempts] == ["mentor"]
        assert attempts[0].context_budget_json == admitted_budget
        assert admitted_budget == dict(
            estimator="utf8-v1",
            estimated_input_tokens=actual_estimate,
            input_budget_tokens=(
                7000
                if trim
                else (
                    1048576
                    if provider == "google"
                    else (1000000 if provider == "anthropic" else 400000) - 1024
                )
            ),
            reserved_output_tokens=1024,
            configured_prior_turn_limit=2,
            selected_prior_pairs=prior_pairs,
            kept_prior_pairs=prior_pairs - int(trim),
            dropped_prior_pairs=1 if trim else 0,
            target_pair_included=True,
            capability_definition_version=version,
        )
        assert attempts[0].input_tokens != actual_estimate
        assert (
            "context_budget" not in result.text
            and "estimated_input_tokens" not in result.text
        )
        assert all(
            row.status == "completed"
            and row.run_id == run.id
            and row.attempt_no == 1
            for row in attempts
        )

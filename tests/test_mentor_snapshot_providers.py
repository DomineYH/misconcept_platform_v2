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
from src.services.lesson_snapshots import canonical_hash
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key

__all__ = ["client"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
@pytest.mark.parametrize("trigger", ["manual", "auto"])
async def test_mentor_uses_one_frozen_provider_config_for_all_subcalls(
    data, client, monkeypatch, provider, trigger
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
    model.verification_state = {"student": evidence, "mentor": evidence}
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
                turn_index=1,
            )
            for role, content in [
                ("teacher", "Known teacher"),
                ("student", "Known student"),
            ]
        ]
    )
    await data.db.commit()

    def content():
        return (
            '{"is_repetitive":true,"is_inappropriate":false,"reason":"Author condition"}'
            if trigger == "auto" and len(calls) == 1
            else "Frozen coaching"
        )

    if provider == "openai":

        async def upstream(request, body):
            return httpx2.Response(200, json=response_body(content()))

        clients, calls = sdk_transport(
            monkeypatch, upstream, budget=1024, key=KEY
        )
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport as claude_transport
        from test_anthropic_probes import response_body as claude_response

        async def upstream(request):
            return httpx2.Response(200, json=claude_response(content()))

        clients, calls = claude_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:
        from test_google_catalog import install
        from test_google_invocations import response

        async def upstream(request):
            return httpx.Response(200, json=response(content()))

        clients, calls = install(monkeypatch, upstream)
    login(client, data.owner)
    request_id = str(uuid4())
    result = await client.post(
        f"/sessions/{data.session.id}/turns/{turn_id}/mentor/stream",
        json=dict(request_id=request_id, trigger=trigger),
    )
    assert (
        frames(result)[-1][1]["message"]["content"] == "Frozen coaching"
    ), result.text
    assert len(calls) == (2 if trigger == "auto" else 1)
    for call in calls:
        body = call if provider == "openai" else json.loads(call.content)
        if provider == "openai":
            assert body["model"] == "gpt-5-mini"
            assert body["max_output_tokens"] == 1024
            assert body["reasoning"] == {"effort": "low"}
            instruction = body["instructions"]
        elif provider == "anthropic":
            assert body["model"] == "claude-sonnet-4-6"
            assert body["max_tokens"] == 1024
            assert body["thinking"] == {"type": "disabled"}
            assert body["temperature"] == 0.4
            instruction = body["system"]
        else:
            assert call.url.path.endswith("gemini-2.5-flash:generateContent")
            assert body["generationConfig"]["maxOutputTokens"] == 1024
            assert (
                body["generationConfig"]["thinkingConfig"]["thinking_budget"]
                == 0
            )
            assert body["generationConfig"]["temperature"] == 0.4
            instruction = body["systemInstruction"]["parts"][0]["text"]
        assert "Coach {literal}" in instruction
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
        attempts = (
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        ).all()
        assert [row.operation for row in attempts] == (
            ["mentor_judgment", "mentor"] if trigger == "auto" else ["mentor"]
        )
        assert all(
            row.status == "completed"
            and row.run_id == run.id
            and row.attempt_no == 1
            for row in attempts
        )

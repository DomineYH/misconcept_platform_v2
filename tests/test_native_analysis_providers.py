"""Analysis subcalls retain native options and the selected provider's limit."""

import copy
import json

import httpx
import httpx2
import pytest
from s4_analysis_fixtures import analysis_reply
from sqlalchemy import select
from test_native_analysis import api as analysis_api
from test_native_analysis import connection_api as provider_api
from test_native_analysis import native_analysis
from test_provider_connections import KEY

from src.models import ApiUsageLog, AppSetting, Message, ProviderConnection
from src.services.lesson_snapshots import canonical_hash
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_analysis_subcalls_use_selected_provider_options_and_capacity(
    data, api, monkeypatch, provider
):
    connection, model = await native_analysis(data, monkeypatch)
    source = connection
    connection = await data.db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == provider
        )
    )
    connection.enabled = True
    connection.credential_revision = 1
    connection.encryption_key_version = source.encryption_key_version
    connection.masked_hint = "-KEY"
    model.provider_connection_id = connection.id
    model.model_id = {
        "openai": "gpt-5-mini",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-2.5-flash",
    }[provider]
    definition = capabilities(provider, model.model_id)["definition_version"]
    model.capability_definition_version = definition
    evidence = dict(
        status="succeeded",
        credential_revision=1,
        connection_version=connection.connection_version,
        capability_definition_version=definition,
        role_contract_version="s4-v2",
    )
    model.verification_state = {"analysis": evidence}
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
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"]["resolved_model_config"].update(
        provider_connection_id=connection.id,
        provider=provider,
        model_id=model.model_id,
        options=options,
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    model.default_options_json = {"unknown_current_option": True}
    setting = await data.db.get(AppSetting, 1)
    setting.limits_json = dict(
        total=8, openai=2, anthropic=2, google=2, admin=1
    )
    setting.limits_json = {**setting.limits_json, provider: 3}
    data.db.add(
        Message(
            session_id=data.session.id, role="teacher", content="Third question"
        )
    )
    await data.db.commit()
    messages = [
        dict(id=m.id, role=m.role, content=m.content)
        for m in await data.db.scalars(select(Message).order_by(Message.id))
    ]

    async def answer(schema):
        assert "message_classifications" in schema["properties"]
        assert "confidence" not in schema["properties"]
        return analysis_reply(messages)

    if provider == "openai":
        from test_student_probe import response_body, sdk_transport

        async def upstream(request, body):
            value = await answer(body["text"]["format"]["schema"])
            return httpx2.Response(200, json=response_body(json.dumps(value)))

        clients, calls = sdk_transport(
            monkeypatch, upstream, budget=1024, key=KEY
        )
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport
        from test_anthropic_probes import response_body

        async def upstream(request):
            body = json.loads(request.content)
            value = await answer(body["output_config"]["format"]["schema"])
            return httpx2.Response(200, json=response_body(json.dumps(value)))

        clients, calls = sdk_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:
        from test_google_catalog import install
        from test_google_invocations import response as google_response

        async def upstream(request):
            body = json.loads(request.content)
            value = await answer(body["generationConfig"]["responseJsonSchema"])
            return httpx.Response(200, json=google_response(json.dumps(value)))

        clients, calls = install(monkeypatch, upstream)

    result = await api.post(
        f"/sessions/{data.session.id}/analyze",
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert result.status_code == 200 and result.json()["distribution"] == {
        "A": 3,
        "B": 0,
        "C": 0,
    }
    assert len(calls) == 1
    for call in calls:
        body = call if provider == "openai" else json.loads(call.content)
        if provider == "openai":
            assert (
                body["model"] == model.model_id
                and body["max_output_tokens"] == 1024
                and body["reasoning"] == {"effort": "low"}
            )
        elif provider == "anthropic":
            assert (
                body["model"] == model.model_id
                and body["max_tokens"] == 1024
                and body["thinking"] == {"type": "disabled"}
                and body["temperature"] == 0.4
            )
        else:
            assert call.url.path.endswith("gemini-2.5-flash:generateContent")
            assert (
                body["generationConfig"]["maxOutputTokens"] == 1024
                and body["generationConfig"]["thinkingConfig"][
                    "thinking_budget"
                ]
                == 0
                and body["generationConfig"]["temperature"] == 0.4
            )
    assert all(
        (c.is_closed if provider == "google" else c.is_closed())
        for c in clients
    )
    async with data.factory() as db:
        attempts = (await db.scalars(select(ApiUsageLog))).all()
        assert len(attempts) == 1 and all(
            a.provider == provider
            and a.model == model.model_id
            and a.status == "completed"
            and a.context_budget_json is None
            for a in attempts
        )

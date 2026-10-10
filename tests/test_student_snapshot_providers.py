"""Native student roles through all three pinned SDK mock transports."""

import json
from uuid import uuid4

import httpx
import httpx2
import pytest
from lesson_fixtures import install_connection, install_snapshot
from sqlalchemy import select
from test_scenario_api import client, login

from src.models import ApiUsageLog, GenerationRun
from src.services.model_capabilities import capabilities
from src.services.provider_secrets import encrypt_key
from src.services.student_bot import StudentBot

__all__ = ["client"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
@pytest.mark.parametrize("stream", [True, False])
async def test_student_uses_frozen_provider_and_native_options(
    data, client, monkeypatch, provider, stream
):
    from test_provider_connections import KEY
    from test_student_generation import frames
    from test_student_probe import response_body, sdk_transport, sse

    connection, model = await install_connection(data, monkeypatch)
    connection.provider = provider
    model.model_id = dict(
        openai="gpt-5-mini",
        anthropic="claude-sonnet-4-6",
        google="gemini-2.5-flash",
    )[provider]
    version = capabilities(provider, model.model_id)["definition_version"]
    model.capability_definition_version = version
    model.verification_state = {
        "student": dict(
            status="succeeded",
            credential_revision=1,
            connection_version=connection.connection_version,
            capability_definition_version=version,
            role_contract_version="s1-v1",
        )
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
    model.default_options_json = {"unknown_current_option": True}
    model.config_version += 1
    await data.db.commit()
    request_id = str(uuid4())
    if provider == "openai":

        async def upstream(request, body):
            if stream:
                payload = sse(
                    "response.output_text.delta",
                    delta="Answer",
                    sequence_number=0,
                    item_id="m",
                    output_index=0,
                    content_index=0,
                )
                payload += sse(
                    "response.completed",
                    response=response_body("Answer"),
                    sequence_number=1,
                )
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=payload,
                )
            return httpx2.Response(200, json=response_body("Answer"))

        clients, calls = sdk_transport(
            monkeypatch, upstream, budget=1024, key=KEY
        )
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport as claude_transport
        from test_anthropic_probes import response_body as claude_response
        from test_anthropic_probes import stream_body

        async def upstream(request):
            if stream:
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=stream_body("Answer"),
                )
            return httpx2.Response(200, json=claude_response("Answer"))

        clients, calls = claude_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:
        from test_google_catalog import install
        from test_google_invocations import response
        from test_google_invocations import sse as google_sse

        async def upstream(request):
            if stream:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=google_sse(response("Answer")),
                )
            return httpx.Response(200, json=response("Answer"))

        clients, calls = install(monkeypatch, upstream)
    if stream:
        login(client, data.owner)
        result = await client.post(
            f"/sessions/{data.session.id}/turns/stream",
            json=dict(request_id=request_id, content="Why {x}?"),
        )
        assert frames(result)[-1][0] == "output.completed", result.text
        assert frames(result)[-1][1]["message"]["content"] == "Answer"
        async with data.factory() as db:
            run = (await db.scalars(select(GenerationRun))).one()
            assert (run.provider, run.model, run.config_hash) == (
                provider,
                model.model_id,
                data.session.config_hash,
            )
    else:
        async with StudentBot(
            data.db, session_id=data.session.id, owner_id=data.owner.id
        ) as bot:
            text, _ = await bot.generate_response("Why {x}?")
            assert text == "Answer"
    assert len(calls) == 1
    body = calls[0] if provider == "openai" else json.loads(calls[0].content)
    if provider == "openai":
        assert body["reasoning"] == {"effort": "low"}
        assert body["max_output_tokens"] == 1024
        instruction = body["instructions"]
    elif provider == "anthropic":
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == 1024
        assert body["thinking"] == {"type": "disabled"}
        assert body["temperature"] == 0.4
        instruction = body["system"]
    else:
        assert calls[0].url.path.endswith(
            "gemini-2.5-flash:"
            + ("streamGenerateContent" if stream else "generateContent")
        )
        assert body["generationConfig"]["maxOutputTokens"] == 1024
        assert (
            body["generationConfig"]["thinkingConfig"]["thinking_budget"] == 0
        )
        assert body["generationConfig"]["temperature"] == 0.4
        instruction = body["systemInstruction"]["parts"][0]["text"]
    assert "Explain your thinking" in instruction
    assert "PRIVATE ANALYSIS" not in instruction
    assert all(
        (sdk.is_closed if provider == "google" else sdk.is_closed())
        for sdk in clients
    )
    async with data.factory() as db:
        attempt = (await db.scalars(select(ApiUsageLog))).one()
        assert (attempt.provider, attempt.model, attempt.status) == (
            provider,
            model.model_id,
            "completed",
        )
        assert attempt.session_id == data.session.id
        assert (attempt.run_id is not None) == stream

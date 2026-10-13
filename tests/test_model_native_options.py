"""Approved model options at the admin API and actual SDK HTTP boundaries."""

import json

import httpx
import httpx2
import pytest
from test_anthropic_probes import response_body as claude_response
from test_anthropic_probes import stream_body as claude_stream
from test_google_catalog import install as install_google
from test_google_invocations import response as google_response
from test_google_invocations import sse as google_stream
from test_model_management import write
from test_provider_connections import api as provider_api
from test_student_probe import response_body as openai_response
from test_student_probe import sse

from src.services import (
    anthropic_generation,
    openai_generation,
)

api = provider_api
OPENAI = [
    (
        "gpt-6-astra",
        ["low", "medium", "high", "xhigh", "max"],
        None,
        1050000,
    ),
    (
        "gpt-6.1-sol",
        ["low", "medium", "high", "xhigh", "max"],
        "medium",
        1050000,
    ),
    (
        "gpt-6-sol",
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        1050000,
    ),
    (
        "gpt-6-luna",
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        1050000,
    ),
    (
        "gpt-5.6-sol",
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        1050000,
    ),
    (
        "gpt-5.6-terra",
        ["none", "low", "medium", "high", "xhigh", "max"],
        "medium",
        1050000,
    ),
    ("gpt-5.5", ["none", "low", "medium", "high", "xhigh"], "medium", 1050000),
    ("gpt-5.2", ["none", "low", "medium", "high", "xhigh"], "none", 400000),
    ("gpt-5-mini", ["minimal", "low", "medium", "high"], "medium", 400000),
    (
        "gpt-5.2-2025-12-11",
        ["none", "low", "medium", "high", "xhigh"],
        "none",
        400000,
    ),
    (
        "gpt-5-mini-2025-08-07",
        ["minimal", "low", "medium", "high"],
        "medium",
        400000,
    ),
]
CLAUDE = [
    ("claude-opus-5-5", ["low", "medium", "high", "xhigh", "max"], "medium"),
    ("claude-sonnet-5-5", ["low", "medium", "high", "xhigh", "max"], "high"),
    ("claude-haiku-5-5", ["low", "medium", "high", "xhigh", "max"], "medium"),
    ("claude-sonnet-4-6", ["low", "medium", "high", "max"], "high"),
]
GOOGLE = [
    ("gemini-3.8-flash", ["low", "medium", "high"], "medium"),
    ("gemini-3.6-flash", ["minimal", "low", "medium", "high"], "medium"),
    ("gemini-3.5-flash-lite", ["minimal", "low", "medium", "high"], "minimal"),
    ("gemini-3.1-flash-lite", ["minimal", "low", "medium", "high"], "minimal"),
    ("gemini-3.1-pro-preview", ["low", "medium", "high"], "high"),
    ("gemini-2.5-flash", None, -1),
]


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_all_approved_models_expose_exact_native_choices_and_save(
    data, api
):
    for provider, definitions, name, maximum in [
        ("openai", OPENAI, "reasoning.effort", 128000),
        ("anthropic", CLAUDE, "output_config.effort", 128000),
        ("google", GOOGLE, "thinking.level", 65536),
    ]:
        for model_id, choices, default, *context in definitions:
            assert (
                await write(
                    api,
                    "models",
                    provider=provider,
                    model_id=model_id,
                    display_name=model_id,
                )
            ).status_code == 200
            model = (await api.get("/admin/ai/state")).json()["models"][-1]
            caps = model["capabilities"]
            assert (
                caps
                and caps["text"]
                and caps["streaming"]
                and caps["structured"]
            )
            assert (
                caps["checked_at"] == caps["limits_checked_at"] == "2026-10-13"
            )
            assert caps["definition_version"] == f"{provider}-2026-10-13-v4"
            assert caps["sources"] and caps["limit_sources"]
            assert caps["max_output_tokens"] == maximum
            if context:
                assert caps["combined_context_tokens"] == context[0]
            if provider == "google":
                assert caps["input_token_limit"] == 1048576
                assert caps["preview"] is (model_id == "gemini-3.1-pro-preview")
            field_name = name if choices else "thinking.budget"
            field = next(f for f in caps["fields"] if f["name"] == field_name)
            assert field["default"] == default
            if choices:
                assert field["choices"] == choices
                outer, inner = name.split(".")
                options = {outer: {inner: choices[-1]}}
            else:
                assert field["min"] == -1 and field["max"] == 24576
                options = {"thinking": {"budget": -1}}
            assert (
                await write(
                    api,
                    f"models/{model['id']}/update",
                    expected_version=1,
                    display_name=model_id,
                    enabled=False,
                    default_options=options,
                )
            ).status_code == 200
            saved = (await api.get("/admin/ai/state")).json()["models"][-1]
            assert saved["default_options"] == options
            assert all(
                v["status"] == "unverified"
                for v in saved["verification_state"].values()
            )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
async def test_claude_xhigh_catalog_metadata_blocks_probe_without_generation(
    data, api, monkeypatch
):
    from uuid import uuid4

    from test_anthropic_catalog import model_info, sdk_transport
    from test_provider_connections import KEY, PASSWORD

    await write(
        api,
        "providers/anthropic/key",
        expected_version=1,
        current_password=PASSWORD,
        api_key=KEY,
    )
    await write(
        api,
        "models",
        provider="anthropic",
        model_id="claude-opus-5-5",
        display_name="Opus",
    )
    metadata = model_info("claude-opus-5-5")
    metadata["capabilities"]["effort"]["xhigh"] = {"supported": False}
    metadata["capabilities"]["thinking"]["types"]["disabled"] = {
        "supported": False
    }

    async def upstream(request):
        return httpx2.Response(
            200,
            json={
                "data": [metadata],
                "has_more": False,
                "first_id": metadata["id"],
                "last_id": metadata["id"],
            },
        )

    _, calls = sdk_transport(monkeypatch, upstream)
    assert (
        await write(api, "providers/anthropic/catalog", expected_version=2)
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert state["providers"][1]["catalog"]["models"][0]["capabilities"][
        "effort"
    ]["xhigh"] == {"supported": False}
    assert state["models"][0]["capabilities"]["metadata_conflict"]

    def no_generation(**kwargs):
        pytest.fail("metadata conflict must block before SDK construction")

    monkeypatch.setattr(anthropic_generation, "AsyncAnthropic", no_generation)
    assert (
        await write(
            api,
            "models/1/probes",
            expected_version=1,
            role="student",
            request_id=str(uuid4()),
        )
    ).status_code == 422
    assert len(calls) == 1
    metadata["capabilities"]["effort"]["xhigh"] = {"supported": True}
    assert (
        await write(api, "providers/anthropic/catalog", expected_version=2)
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert not state["models"][0]["capabilities"][
        "metadata_conflict"
    ], "unsupported disabled is not promised on Opus"
    assert (
        state["models"][0]["verification_state"]["student"]["status"]
        == "unverified"
    )


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-6.1-sol"),
        ("anthropic", "claude-sonnet-5-5"),
        ("google", "gemini-3.8-flash"),
    ],
)
async def test_successful_empty_catalog_replaces_list_and_preserves_registered_config(
    data, api, monkeypatch, provider, model
):
    from test_anthropic_catalog import (
        model_info,
    )
    from test_anthropic_catalog import (
        sdk_transport as claude_catalog,
    )
    from test_google_catalog import model as google_model
    from test_openai_catalog import model_list
    from test_openai_catalog import sdk_transport as openai_catalog
    from test_provider_connections import KEY, PASSWORD

    assert (
        await write(
            api,
            f"providers/{provider}/key",
            expected_version=1,
            current_password=PASSWORD,
            api_key=KEY,
        )
    ).status_code == 200
    assert (
        await write(
            api, "models", provider=provider, model_id=model, display_name=model
        )
    ).status_code == 200
    if provider == "openai":
        values = model_list(model)

        async def upstream(request):
            return httpx2.Response(200, json=values)

        _, calls = openai_catalog(monkeypatch, upstream)
    elif provider == "anthropic":
        values = {
            "data": [model_info(model)],
            "has_more": False,
            "first_id": model,
            "last_id": model,
        }

        async def upstream(request):
            return httpx2.Response(200, json=values)

        _, calls = claude_catalog(monkeypatch, upstream)
    else:
        values = {"models": [google_model(f"models/{model}")]}

        async def upstream(request):
            return httpx.Response(200, json=values)

        _, calls = install_google(monkeypatch, upstream)
    assert (
        await write(api, f"providers/{provider}/catalog", expected_version=2)
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    saved = state["models"]
    index = ["openai", "anthropic", "google"].index(provider)
    assert state["providers"][index]["catalog"]["models"]
    values.clear()
    values.update(
        {"object": "list", "data": []}
        if provider == "openai"
        else (
            {"has_more": False, "data": [], "first_id": None, "last_id": None}
            if provider == "anthropic"
            else {"models": []}
        )
    )
    assert (
        await write(api, f"providers/{provider}/catalog", expected_version=2)
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert state["providers"][index]["catalog"]["models"] == []
    assert state["providers"][index]["catalog"]["fetched_at"]
    assert not state["providers"][index]["catalog"]["stale"]
    assert state["models"] == saved
    assert len(calls) == 2


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "provider,model,options",
    [
        ("openai", "gpt-6.1-sol", {"reasoning": {"effort": "max"}}),
        (
            "anthropic",
            "claude-sonnet-5-5",
            {"output_config": {"effort": "xhigh"}},
        ),
        ("google", "gemini-3.5-flash-lite", {"thinking": {"level": "minimal"}}),
    ],
)
@pytest.mark.parametrize(
    "role,budget", [("student", 1024), ("mentor", 1500), ("analysis", 2500)]
)
async def test_new_native_options_survive_explicit_role_probes(
    data, api, monkeypatch, provider, model, options, role, budget
):
    from uuid import uuid4

    from test_anthropic_catalog import sdk_transport as claude_transport
    from test_provider_connections import KEY, PASSWORD
    from test_role_probes import (
        CLASSIFICATION,
        MENTOR_NEGATIVE,
        MENTOR_POSITIVE,
        SYNTHESIS,
    )
    from test_student_probe import completed
    from test_student_probe import sdk_transport as openai_transport

    from src.services.probe_execution import stop_probes

    assert (
        await write(
            api,
            f"providers/{provider}/key",
            expected_version=1,
            current_password=PASSWORD,
            api_key=KEY,
        )
    ).status_code == 200
    assert (
        await write(
            api, "models", provider=provider, model_id=model, display_name=model
        )
    ).status_code == 200
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name=model,
            enabled=False,
            default_options={"max_output_tokens": 4096, **options},
        )
    ).status_code == 200

    def content():
        if role == "student":
            return "Synthetic answer"
        return json.dumps(
            (MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE)
            if role == "mentor"
            else CLASSIFICATION if len(calls) == 1 else SYNTHESIS
        )

    if provider == "openai":

        async def upstream(request, body):
            assert body["reasoning"] == {"effort": "max"}
            payload = openai_response(content())
            payload["model"] = model
            return (
                httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=sse(
                        "response.completed",
                        response=payload,
                        sequence_number=0,
                    ),
                )
                if body.get("stream")
                else httpx2.Response(200, json=payload)
            )

        _, calls = openai_transport(
            monkeypatch, upstream, budget=budget, model=model
        )
    elif provider == "anthropic":

        async def upstream(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == budget
            assert body["output_config"]["effort"] == "xhigh"
            assert "thinking" not in body
            return (
                httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=claude_stream(content()),
                )
                if body.get("stream")
                else httpx2.Response(
                    200, json=claude_response(content(), model=model)
                )
            )

        _, calls = claude_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:

        async def upstream(request):
            body = json.loads(request.content)
            assert body["generationConfig"]["maxOutputTokens"] == budget
            assert body["generationConfig"]["thinkingConfig"] == {
                "thinking_level": "MINIMAL"
            }
            payload = google_response(content())
            payload["modelVersion"] = model
            return (
                httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=google_stream(payload),
                )
                if request.url.path.endswith(":streamGenerateContent")
                else httpx.Response(200, json=payload)
            )

        _, calls = install_google(monkeypatch, upstream)
    request_id = str(uuid4())
    try:
        assert (
            await write(
                api,
                "models/1/probes",
                expected_version=2,
                role=role,
                request_id=request_id,
            )
        ).status_code == 202
        assert (await completed(api, request_id))["status"] == "succeeded"
        assert len(calls) == 2
        saved = (await api.get("/admin/ai/state")).json()["models"][0]
        assert saved["default_options"] == {
            "max_output_tokens": 4096,
            **options,
        }
        assert saved["probe_budgets"][role] == budget
    finally:
        await stop_probes()


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "provider,model,options",
    [
        ("openai", "gpt-6.1-sol", {"reasoning": {"effort": "none"}}),
        (
            "anthropic",
            "claude-sonnet-5-5",
            {
                "thinking": {"type": "between_tools"},
                "output_config": {"effort": "xhigh"},
            },
        ),
        ("google", "gemini-3.8-flash", {"thinking": {"level": "minimal"}}),
    ],
)
async def test_unsupported_saved_native_options_reject_all_probes_before_sdk(
    data, api, monkeypatch, provider, model, options
):
    from uuid import uuid4

    from test_provider_connections import KEY, PASSWORD

    from src.models.model_config import ModelConfig

    assert (
        await write(
            api,
            f"providers/{provider}/key",
            expected_version=1,
            current_password=PASSWORD,
            api_key=KEY,
        )
    ).status_code == 200
    assert (
        await write(
            api, "models", provider=provider, model_id=model, display_name=model
        )
    ).status_code == 200
    async with data.factory() as db:
        stored = await db.get(ModelConfig, 1)
        stored.default_options_json = options
        await db.commit()

    def no_generation(**kwargs):
        pytest.fail(
            "invalid saved option must fail before provider SDK construction"
        )

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", no_generation)
    monkeypatch.setattr(anthropic_generation, "AsyncAnthropic", no_generation)
    from src.services import google_client

    monkeypatch.setattr(google_client, "Client", no_generation)
    for role in ["student", "mentor", "analysis"]:
        assert (
            await write(
                api,
                "models/1/probes",
                expected_version=1,
                role=role,
                request_id=str(uuid4()),
            )
        ).status_code == 422
    saved = (await api.get("/admin/ai/state")).json()["models"][0]
    assert saved["default_options"] == options
    assert saved["probe_budgets"] == {}

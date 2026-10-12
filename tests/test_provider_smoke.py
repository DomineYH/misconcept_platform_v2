"""Operator CLI with synthetic SQLite and SDK transport mocks only."""

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.schemas.ai_settings import CallLimits, CallTimeouts
from src.config import config
from src.db.connection import Base
from src.models import AppSetting, ModelConfig, ProviderConnection, User
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import capabilities
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.services.provider_secrets import encrypt_key


@pytest.fixture
async def smoke_db(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        config,
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        SecretStr(base64.b64encode(bytes(range(32))).decode()),
    )
    monkeypatch.setattr(
        config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "synthetic-v1"
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(
            User(
                id=1, username="synthetic-admin", nickname="Admin", role="admin"
            )
        )
        connection = ProviderConnection(
            id=1,
            provider="openai",
            enabled=True,
            credential_revision=1,
            connection_version=1,
            encryption_key_version="synthetic-v1",
            masked_hint="test",
        )
        connection.encrypted_key, connection.nonce = encrypt_key(
            connection, "SYNTHETIC-SECRET", 1
        )
        db.add(connection)
        definition = capabilities("openai", "gpt-5-mini")
        db.add(
            ModelConfig(
                id=1,
                provider_connection_id=1,
                model_id="gpt-5-mini",
                display_name="synthetic",
                enabled=True,
                capability_definition_version=definition["definition_version"],
                default_options_json={},
                verification_state={
                    role: dict(
                        status="succeeded",
                        credential_revision=1,
                        connection_version=1,
                        capability_definition_version=definition[
                            "definition_version"
                        ],
                        role_contract_version=version,
                    )
                    for role, version in ROLE_CONTRACT_VERSIONS.items()
                },
            )
        )
        db.add(
            AppSetting(
                id=1,
                limits_json=CallLimits(
                    total=24, openai=24, anthropic=24, google=24, admin=3
                ).model_dump(),
                timeouts_json=CallTimeouts(
                    connect=5,
                    student_first_output=30,
                    student_total=60,
                    mentor_first_output=30,
                    mentor_total=60,
                    analysis_total=120,
                    model_list_total=30,
                ).model_dump(),
            )
        )
        await db.commit()
    await engine.dispose()
    return path


async def test_dry_run_has_fixed_synthetic_plan_and_no_database_writes(
    smoke_db,
):
    from src.cli.provider_smoke import run

    before = hashlib.sha256(smoke_db.read_bytes()).hexdigest()
    report = await run(
        smoke_db,
        [
            dict(
                model_config_id=1,
                role="student",
                options={"max_output_tokens": 1024},
            )
        ],
        owner_id=1,
    )
    assert report["mode"] == "dry-run"
    assert report["plan"]["reserved_usd"] == "0.102048"
    assert report["plan"]["calls"][0]["input_tokens_upper"] == 400000
    assert (
        report["plan"]["retry_count"]
        == report["plan"]["probe_count"]
        == report["plan"]["split_count"]
        == 0
    )
    assert report["samples"] == []
    assert "SYNTHETIC-SECRET" not in json.dumps(report)
    assert hashlib.sha256(smoke_db.read_bytes()).hexdigest() == before


async def test_execute_without_human_approval_refuses_before_calls(
    smoke_db, tmp_path
):
    from src.cli.provider_smoke import run

    with pytest.raises(ValueError, match="approval_required"):
        await run(
            smoke_db,
            [
                dict(
                    model_config_id=1,
                    role="student",
                    options={"max_output_tokens": 1024},
                )
            ],
            owner_id=1,
            execute=True,
            output=tmp_path / "report.json",
        )


def selection(role="student", maximum=1024):
    return dict(
        model_config_id=1, role=role, options={"max_output_tokens": maximum}
    )


def approve(report, maximum="3"):
    return dict(
        plan_sha256=report["plan_sha256"],
        approver="Synthetic operator",
        approved_at=datetime.now(timezone.utc).isoformat(),
        max_usd=maximum,
    )


def transport(monkeypatch, handler):
    import httpx2
    from openai import AsyncOpenAI

    from src.services import openai_generation

    calls = []

    async def upstream(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["authorization"] == "Bearer SYNTHETIC-SECRET"
        assert payload["service_tier"] == "default"
        assert payload["store"] is False
        return await handler(request, payload)

    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", client)
    return calls


def paid_response(content="Synthetic answer"):
    return dict(
        id="resp_synthetic",
        object="response",
        created_at=1,
        model="gpt-5-mini",
        status="completed",
        service_tier="default",
        output=[
            dict(
                id="msg_synthetic",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    dict(type="output_text", text=content, annotations=[])
                ],
            )
        ],
        usage=dict(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            input_tokens_details=dict(cached_tokens=0),
            output_tokens_details=dict(reasoning_tokens=0),
        ),
    )


async def test_approved_execution_records_safe_sample_and_consumes_approval(
    smoke_db, tmp_path, monkeypatch
):
    import httpx2
    from s6_measurement import summarize

    from src.cli.provider_smoke import run

    async def handler(request, payload):
        return httpx2.Response(
            200, json=paid_response("RESPONSE-SECRET-SENTINEL")
        )

    calls = transport(monkeypatch, handler)
    plan = await run(smoke_db, [selection()], owner_id=1)
    output = tmp_path / "report.json"
    result = await run(
        smoke_db,
        [selection()],
        owner_id=1,
        execute=True,
        approval=approve(plan),
        output=output,
    )
    assert result["status"] == "completed"
    assert len(calls) == 1
    sample = result["samples"][0]
    assert sample["ledger"][0]["estimated_cost_usd"] == 0.0000125
    assert sample["reserved_usd"] == "0.102048"
    assert summarize(result["samples"])["counts"]["completed"] == 1
    assert json.loads(output.read_text()) == result
    assert "SYNTHETIC-SECRET" not in output.read_text()
    assert "RESPONSE-SECRET-SENTINEL" not in output.read_text()
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await run(
            smoke_db,
            [selection()],
            owner_id=1,
            execute=True,
            approval=approve(plan),
            output=tmp_path / "second.json",
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    ["unknown_usage", "transient", "timeout", "cancel", "bound", "wrong_model"],
)
async def test_first_uncertain_or_failed_call_stops_without_refund(
    smoke_db, tmp_path, monkeypatch, failure
):
    import asyncio

    import httpx2

    from src.cli.provider_smoke import run

    async def handler(request, payload):
        if failure == "transient":
            return httpx2.Response(
                503, json={"error": {"message": "SECRET-ERROR"}}
            )
        if failure == "timeout":
            raise httpx2.ReadTimeout("SECRET-ERROR")
        if failure == "cancel":
            raise asyncio.CancelledError()
        response = paid_response()
        if failure == "unknown_usage":
            response["usage"] = None
        elif failure == "bound":
            response["usage"]["output_tokens"] = 1025
            response["usage"]["total_tokens"] = 1035
        elif failure == "wrong_model":
            response["model"] = "gpt-5.2"
        return httpx2.Response(200, json=response)

    calls = transport(monkeypatch, handler)
    selections = [selection(), selection("mentor")]
    plan = await run(smoke_db, selections, owner_id=1)
    result = await run(
        smoke_db,
        selections,
        owner_id=1,
        execute=True,
        approval=approve(plan),
        output=tmp_path / "failure.json",
    )
    assert result["status"] == "failed"
    assert result["plan"]["reserved_usd"] == "0.204096"
    assert len(result["samples"]) == len(calls) == 1
    assert result["samples"][0]["status"] == (
        "cancelled" if failure == "cancel" else "failed"
    )
    assert "SECRET-ERROR" not in json.dumps(result)
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await run(
            smoke_db,
            selections,
            owner_id=1,
            execute=True,
            approval=approve(plan),
            output=tmp_path / "replay.json",
        )
    assert len(calls) == 1


async def add_model(database, provider, model_id, identifier):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            from sqlalchemy import select

            connection = await db.scalar(
                select(ProviderConnection).where(
                    ProviderConnection.provider == provider
                )
            )
            if connection is None:
                connection = ProviderConnection(
                    id=identifier,
                    provider=provider,
                    enabled=True,
                    credential_revision=1,
                    connection_version=1,
                    encryption_key_version="synthetic-v1",
                    masked_hint="test",
                )
                connection.encrypted_key, connection.nonce = encrypt_key(
                    connection, "SYNTHETIC-SECRET", 1
                )
                db.add(connection)
            definition = capabilities(provider, model_id)
            db.add(
                ModelConfig(
                    id=identifier,
                    provider_connection_id=connection.id,
                    model_id=model_id,
                    display_name="synthetic",
                    enabled=True,
                    capability_definition_version=(
                        definition["definition_version"]
                        if definition
                        else "unknown"
                    ),
                    default_options_json={},
                    verification_state={
                        role: dict(
                            status="succeeded",
                            credential_revision=1,
                            connection_version=1,
                            capability_definition_version=(
                                definition["definition_version"]
                                if definition
                                else "unknown"
                            ),
                            role_contract_version=version,
                        )
                        for role, version in ROLE_CONTRACT_VERSIONS.items()
                    },
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "change", ["hash", "approver", "date", "budget", "missing", "options"]
)
async def test_incomplete_or_mismatched_approval_has_zero_calls(
    smoke_db, tmp_path, monkeypatch, change
):
    from src.cli.provider_smoke import run

    async def forbidden(request, payload):
        pytest.fail("approval refusal must precede SDK entry")

    calls = transport(monkeypatch, forbidden)
    chosen = [selection()]
    plan = await run(smoke_db, chosen, owner_id=1)
    approval = approve(plan)
    if change == "hash":
        approval["plan_sha256"] = "wrong"
    elif change == "approver":
        approval["approver"] = " "
    elif change == "date":
        approval["approved_at"] = "2026-01-01"
    elif change == "budget":
        approval["max_usd"] = "0.102047"
    elif change == "missing":
        del approval["max_usd"]
    else:
        chosen = [selection(maximum=1025)]
    with pytest.raises((ValueError, InvocationError)):
        await run(
            smoke_db,
            chosen,
            owner_id=1,
            execute=True,
            approval=approval,
            output=tmp_path / "refused.json",
        )
    assert calls == []
    assert not (tmp_path / "refused.json").exists()


async def test_three_dollar_boundary_and_multi_provider_aggregate(
    smoke_db, tmp_path, monkeypatch
):
    from src.cli.provider_smoke import run

    async def forbidden(request, payload):
        pytest.fail("planning must not enter SDK")

    calls = transport(monkeypatch, forbidden)
    await add_model(smoke_db, "openai", "gpt-5.2", 2)
    await add_model(smoke_db, "google", "gemini-2.5-flash", 3)
    two = [
        dict(
            model_config_id=2,
            role="student",
            options={"max_output_tokens": 128000},
        ),
        dict(
            model_config_id=3,
            role="student",
            options={"max_output_tokens": 65536, "thinking": {"budget": 0}},
        ),
    ]
    assert (await run(smoke_db, two, owner_id=1))["plan"][
        "reserved_usd"
    ] == "2.9704128"
    with pytest.raises(InvocationError, match="budget_exceeded"):
        await run(
            smoke_db,
            two + [{**two[1], "role": "mentor"}],
            owner_id=1,
            execute=True,
            approval={},
            output=tmp_path / "excess.json",
        )
    exact = [
        dict(model_config_id=2, role=role, options={"max_output_tokens": 50000})
        for role in ("student", "mentor")
    ] + [selection("analysis", 50000)]
    assert (await run(smoke_db, exact, owner_id=1))["plan"][
        "reserved_usd"
    ] == "3.0"
    with pytest.raises(InvocationError, match="budget_exceeded"):
        await run(
            smoke_db, exact[:2] + [selection("analysis", 50001)], owner_id=1
        )
    assert calls == []


@pytest.mark.parametrize(
    "provider,model,options",
    [
        ("openai", "unpriced-model", {"max_output_tokens": 1024}),
        ("google", "gemini-2.5-flash", {"max_output_tokens": 1024}),
        ("anthropic", "claude-sonnet-4-6", {"max_output_tokens": 1024}),
    ],
)
async def test_unknown_billing_contract_or_unaffordable_bound_has_zero_calls(
    smoke_db, tmp_path, monkeypatch, provider, model, options
):
    from src.cli.provider_smoke import run

    async def forbidden(request, payload):
        pytest.fail("unknown or unaffordable billing must precede SDK entry")

    calls = transport(monkeypatch, forbidden)
    await add_model(smoke_db, provider, model, 2)
    with pytest.raises((ValueError, InvocationError)):
        await run(
            smoke_db,
            [dict(model_config_id=2, role="student", options=options)],
            owner_id=1,
            execute=True,
            approval={},
            output=tmp_path / "unknown.json",
        )
    assert calls == []
    assert not (tmp_path / "unknown.json").exists()


async def test_each_selected_role_runs_once_with_existing_output_contracts(
    smoke_db, tmp_path, monkeypatch
):
    import httpx2
    from s4_analysis_fixtures import analysis_reply

    from src.cli.provider_smoke import run
    from src.services.role_probe_contract import ANALYSIS_MESSAGES

    output = tmp_path / "roles.json"

    async def handler(request, payload):
        before = json.loads(output.read_text())
        assert before["reservation_ledger_id"]
        assert before["plan"]["reserved_usd"] == "0.306144"
        schema = payload.get("text", {}).get("format", {}).get("name")
        if schema == "MentorOutput":
            content = json.dumps(
                dict(
                    should_intervene=True,
                    feedback="Synthetic coaching",
                    reason_summary="PRIVATE-MENTOR-SENTINEL",
                )
            )
        elif schema == "UnifiedAnalysisOutput":
            content = json.dumps(analysis_reply(ANALYSIS_MESSAGES))
        else:
            content = "Synthetic student"
        return httpx2.Response(200, json=paid_response(content))

    calls = transport(monkeypatch, handler)
    selections = [selection(role) for role in ("student", "mentor", "analysis")]
    plan = await run(smoke_db, selections, owner_id=1)
    result = await run(
        smoke_db,
        selections,
        owner_id=1,
        execute=True,
        approval=approve(plan, "0.306144"),
        output=output,
    )
    assert result["status"] == "completed"
    assert len(calls) == 3
    assert [sample["ledger"][0]["role"] for sample in result["samples"]] == [
        "student",
        "mentor",
        "analysis",
    ]
    assert all(
        sample["ledger"][0]["attempt_no"] == 1 for sample in result["samples"]
    )
    assert (
        result["samples"][1]["durations_ms"]["final_validated_result"]
        is not None
    )
    assert "PRIVATE-MENTOR-SENTINEL" not in output.read_text()


async def test_multiple_providers_share_one_reservation_and_report(
    smoke_db, tmp_path, monkeypatch
):
    import httpx
    import httpx2

    from src.cli.provider_smoke import run
    from src.services import google_client

    async def openai_response(request, payload):
        return httpx2.Response(200, json=paid_response())

    calls = transport(monkeypatch, openai_response)
    google_calls = []
    original = httpx.AsyncClient

    async def google_response(request):
        google_calls.append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "SYNTHETIC-SECRET"
        return httpx.Response(
            200,
            json=dict(
                modelVersion="gemini-2.5-flash",
                candidates=[
                    dict(
                        content=dict(
                            role="model", parts=[dict(text="Synthetic google")]
                        ),
                        finishReason="STOP",
                    )
                ],
                usageMetadata=dict(
                    promptTokenCount=10,
                    candidatesTokenCount=5,
                    thoughtsTokenCount=0,
                    cachedContentTokenCount=0,
                    totalTokenCount=15,
                ),
            ),
        )

    def google_transport(**kwargs):
        return original(
            **kwargs, transport=httpx.MockTransport(google_response)
        )

    monkeypatch.setattr(google_client, "AsyncClient", google_transport)
    await add_model(smoke_db, "google", "gemini-2.5-flash", 2)
    selections = [
        selection(),
        dict(
            model_config_id=2,
            role="student",
            options={"max_output_tokens": 1024, "thinking": {"budget": 0}},
        ),
    ]
    plan = await run(smoke_db, selections, owner_id=1)
    assert plan["plan"]["reserved_usd"] == "0.4191808"
    result = await run(
        smoke_db,
        selections,
        owner_id=1,
        execute=True,
        approval=approve(plan),
        output=tmp_path / "providers.json",
    )
    assert result["status"] == "completed"
    assert len(calls) == len(google_calls) == 1
    assert [row["conditions"]["provider"] for row in result["samples"]] == [
        "openai",
        "google",
    ]


def test_cli_refusal_has_safe_reason_and_nonzero_exit(
    tmp_path, monkeypatch, capsys
):
    import sys

    from src.cli.provider_smoke import main

    selections = tmp_path / "selection.json"
    selections.write_text(json.dumps([selection()]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provider_smoke",
            "--database",
            str(tmp_path / "synthetic.db"),
            "--selection",
            str(selections),
            "--owner-id",
            "1",
            "--execute",
        ],
    )
    assert main() == 1
    assert (
        json.loads(capsys.readouterr().out)["error_code"] == "approval_required"
    )


async def test_configured_runtime_database_is_rejected_before_read(
    smoke_db, monkeypatch
):
    from src.cli.provider_smoke import run

    monkeypatch.setattr(
        config, "DATABASE_URL", f"sqlite+aiosqlite:///{smoke_db}"
    )
    with pytest.raises(ValueError, match="isolated_database_required"):
        await run(smoke_db, [selection()], owner_id=1)


async def test_classroom_records_refuse_all_calls_and_remain_unchanged(
    smoke_db, tmp_path, monkeypatch
):
    from src.cli.provider_smoke import run
    from src.models import Scenario

    engine = create_async_engine(f"sqlite+aiosqlite:///{smoke_db}")
    async with async_sessionmaker(engine)() as db:
        db.add(Scenario(title="SYNTHETIC-CLASSROOM-SENTINEL", config_json={}))
        await db.commit()
    await engine.dispose()
    before = hashlib.sha256(smoke_db.read_bytes()).hexdigest()

    async def forbidden(request, payload):
        pytest.fail("classroom data must be rejected before SDK entry")

    calls = transport(monkeypatch, forbidden)
    with pytest.raises(InvocationError, match="classroom_database_forbidden"):
        await run(
            smoke_db,
            [selection()],
            owner_id=1,
            execute=True,
            approval={},
            output=tmp_path / "classroom.json",
        )
    assert calls == []
    assert hashlib.sha256(smoke_db.read_bytes()).hexdigest() == before


async def test_invalid_mentor_contract_stops_remaining_analysis(
    smoke_db, tmp_path, monkeypatch
):
    import httpx2

    from src.cli.provider_smoke import run

    async def handler(request, payload):
        content = (
            json.dumps(
                dict(
                    should_intervene=False,
                    feedback="",
                    reason_summary="PRIVATE-INVALID-MENTOR",
                )
            )
            if len(calls) == 2
            else "Synthetic student"
        )
        return httpx2.Response(200, json=paid_response(content))

    calls = transport(monkeypatch, handler)
    selections = [selection(role) for role in ("student", "mentor", "analysis")]
    plan = await run(smoke_db, selections, owner_id=1)
    result = await run(
        smoke_db,
        selections,
        owner_id=1,
        execute=True,
        approval=approve(plan),
        output=tmp_path / "invalid.json",
    )
    assert result["status"] == "failed"
    assert len(calls) == 2
    assert [row["status"] for row in result["samples"]] == [
        "completed",
        "failed",
    ]
    assert result["plan"]["reserved_usd"] == "0.306144"
    assert "PRIVATE-INVALID-MENTOR" not in json.dumps(result)


@pytest.mark.parametrize(
    "variable",
    [
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
    ],
)
async def test_ambient_endpoint_override_is_refused_without_calls(
    smoke_db, tmp_path, monkeypatch, variable
):
    from src.cli.provider_smoke import run

    async def forbidden(request, payload):
        pytest.fail(
            "an unapproved endpoint must not receive even a mocked call"
        )

    calls = transport(monkeypatch, forbidden)
    plan = await run(smoke_db, [selection()], owner_id=1)
    monkeypatch.setenv(variable, "https://unapproved.invalid")
    with pytest.raises(ValueError, match="native_environment_required"):
        await run(
            smoke_db,
            [selection()],
            owner_id=1,
            execute=True,
            approval=approve(plan),
            output=tmp_path / "endpoint.json",
        )
    assert calls == []

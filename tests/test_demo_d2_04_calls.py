"""D2-04 PR1: demo request fields, local adapter branch and CLI refusal."""

import argparse
import asyncio
import dataclasses
import json
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from test_anthropic_invocations import REQUEST as ANTHROPIC_REQUEST
from test_demo_config import run_python
from test_demo_provider import prepared as demo_prepared
from test_google_invocations import REQUEST as GOOGLE_REQUEST
from test_openai_invocations import REQUEST as OPENAI_REQUEST

from src.cli import provider_smoke
from src.services import (
    anthropic_catalog,
    anthropic_generation,
    call_execution,
    google_catalog,
    google_generation,
    openai_catalog,
    openai_generation,
)
from src.services.call_admission import (
    CallPermit,
    active_calls,
    registered_calls,
)
from src.services.invocation_types import StructuredRequest, TextRequest

prepared = demo_prepared
EXTERNAL = {
    "openai": (openai_catalog, openai_generation),
    "anthropic": (anthropic_catalog, anthropic_generation),
    "google": (google_catalog, google_generation),
}
TIMEOUTS = dict(
    student_total=5, mentor_total=5, analysis_total=5, model_list_total=5
)


def test_demo_fields_are_keyword_only_and_default_to_none():
    for cls in (TextRequest, StructuredRequest):
        fields = {f.name: f for f in dataclasses.fields(cls)}
        for name in ("demo_context", "demo_selection"):
            assert fields[name].kw_only and fields[name].default is None
    request = TextRequest("openai", "m", "student", "s", [], {}, "r")
    assert request.demo_context is None and request.demo_selection is None
    with pytest.raises(TypeError):
        TextRequest("openai", "m", "student", "s", [], {}, "r", None, None)


@pytest.mark.parametrize(
    "request_",
    [OPENAI_REQUEST, ANTHROPIC_REQUEST, GOOGLE_REQUEST],
    ids=["openai", "anthropic", "google"],
)
def test_demo_fields_never_reach_an_external_payload(request_):
    module = EXTERNAL[request_.provider][1]
    marked = dataclasses.replace(
        request_, demo_context=object(), demo_selection=object()
    )
    assert module.parameters(marked) == module.parameters(request_)


@pytest.fixture
def boundary(monkeypatch):
    """Record SDK, ledger and recheck reach; PR2/PR3 own those for demo."""
    seen = SimpleNamespace(sdk=[], ledger=[], finished=[], rechecks=0)

    def forbidden(name):
        def call(*args, **kwargs):
            seen.sdk.append(name)
            raise AssertionError(f"external SDK reached: {name}")

        return call

    for provider, (catalog, generation) in EXTERNAL.items():
        monkeypatch.setattr(
            catalog, "list_models", forbidden(f"{provider}.catalog")
        )
        for name in ("generate_text", "stream_text", "generate_structured"):
            monkeypatch.setattr(
                generation, name, forbidden(f"{provider}.{name}")
            )

    async def start_attempt(factory, **values):
        seen.ledger.append(values)
        return len(seen.ledger)

    async def finish_attempt(factory, entry_id, **values):
        seen.finished.append(values)
        return True

    async def recheck_call(permit):
        seen.rechecks += 1

    async def load_active_lesson(db, session_id, owner_id):
        return None

    monkeypatch.setattr(call_execution, "start_attempt", start_attempt)
    monkeypatch.setattr(call_execution, "finish_attempt", finish_attempt)
    monkeypatch.setattr(call_execution, "recheck_call", recheck_call)
    monkeypatch.setattr(
        call_execution, "load_active_lesson", load_active_lesson
    )
    return seen


def permit(factory, *, provider="demo", operation="student", secret=None):
    value = CallPermit(
        factory,
        1,
        provider,
        1,
        1,
        operation,
        "student",
        False,
        TIMEOUTS,
        secret,
        asyncio.get_running_loop().time(),
        asyncio.current_task(),
        1,
        model_id="demo-classroom-v1",
    )
    active_calls.add(value)
    registered_calls.add(value)
    return value


def demo_request(p, **changes):
    request = TextRequest(
        **{
            f.name: getattr(p.request, f.name)
            for f in dataclasses.fields(TextRequest)
        }
    )
    return dataclasses.replace(request, **changes)


async def consume(call_permit, request, **kwargs):
    async with aclosing(
        call_execution.execute_call(call_permit, request, **kwargs)
    ) as events:
        return [event async for event in events]


async def test_demo_adapter_streams_through_the_common_boundary(
    prepared, boundary
):
    p = prepared
    expected = await p.provider.generate_text(p.request, None, TIMEOUTS)
    call_permit = permit(p.factory)
    events = await consume(
        call_permit,
        demo_request(p),
        kind="stream",
        session_id=2,
        app_mode="demo",
        demo_adapter=p.provider,
    )
    assert events[-1].type == "completed"
    assert events[-1].text == expected.text
    assert "".join(e.text for e in events if e.type == "text_delta") == (
        expected.text
    )
    assert events[-1].usage["usage_complete"] is False
    assert [entry["provider"] for entry in boundary.ledger] == ["demo"]
    assert boundary.finished[0]["status"] == "completed"
    assert boundary.rechecks == 1
    assert boundary.sdk == []
    assert call_permit not in registered_calls
    assert call_permit.secret is None


@pytest.mark.parametrize("kind", ["text", "structured"])
async def test_demo_text_and_structured_kinds_use_only_the_local_adapter(
    prepared, boundary, kind
):
    p = prepared
    events = await consume(
        permit(p.factory),
        demo_request(p),
        kind=kind,
        app_mode="demo",
        demo_adapter=p.provider,
    )
    # A text request is not a structured request; the adapter itself refuses.
    assert events[-1].type == ("completed" if kind == "text" else "error")
    assert boundary.sdk == []


@pytest.mark.parametrize("provider", sorted(EXTERNAL))
@pytest.mark.parametrize("kind", ["text", "stream", "structured", "catalog"])
async def test_demo_refuses_external_providers_before_any_sdk_or_ledger(
    prepared, boundary, provider, kind
):
    p = prepared
    request = None
    if kind != "catalog":
        request = demo_request(p, provider=provider)
    events = await consume(
        permit(
            p.factory,
            provider=provider,
            operation="model_list" if kind == "catalog" else "student",
            secret="SYNTHETIC-SECRET",
        ),
        request,
        kind=kind,
        app_mode="demo",
        demo_adapter=p.provider,
    )
    assert [(e.type, e.error_code) for e in events] == [
        ("error", "configuration_unavailable")
    ]
    assert boundary.sdk == boundary.ledger == []
    assert p.visitors.calls == 0


def fake_adapter(p):
    async def generate_text(*args, **kwargs):
        raise AssertionError("fake adapter reached")

    return SimpleNamespace(mode="demo", generate_text=generate_text)


def lease_released(p):
    return dataclasses.replace(p.context, lease=SimpleNamespace(released=True))


DEMO_REFUSALS = {
    "secret": dict(secret="SYNTHETIC-SECRET"),
    "model_list": dict(operation="model_list"),
    "probe": dict(operation="probe"),
    "probe_step": dict(call=dict(probe_step="student")),
    "comparison": dict(call=dict(usage_scope="comparison")),
    "no_context": dict(request=dict(demo_context=None)),
    "no_selection": dict(request=dict(demo_selection=None)),
    "plain_context": dict(request=dict(demo_context=SimpleNamespace())),
    "released_lease": dict(
        request=lambda p: dict(demo_context=lease_released(p))
    ),
    "request_provider": dict(request=dict(provider="openai")),
    "no_adapter": dict(call=dict(demo_adapter=None)),
    "fake_adapter": dict(call=lambda p: dict(demo_adapter=fake_adapter(p))),
}


@pytest.mark.parametrize("case", sorted(DEMO_REFUSALS))
async def test_demo_rechecks_mode_provider_context_and_secret_independently(
    prepared, boundary, case
):
    p = prepared
    spec = DEMO_REFUSALS[case]

    def resolve(value):
        return value(p) if callable(value) else value

    call = dict(app_mode="demo", demo_adapter=p.provider)
    call.update(resolve(spec.get("call", {})))
    events = await consume(
        permit(
            p.factory,
            operation=spec.get("operation", "student"),
            secret=spec.get("secret"),
        ),
        demo_request(p, **resolve(spec.get("request", {}))),
        kind="text",
        **call,
    )
    assert [(e.type, e.error_code) for e in events] == [
        ("error", "configuration_unavailable")
    ]
    assert boundary.sdk == boundary.ledger == []
    assert p.visitors.calls == 0


@pytest.mark.parametrize(
    "case", ["adapter_supplied", "demo_provider", "demo_fields"]
)
async def test_normal_never_reaches_the_demo_adapter(prepared, boundary, case):
    p = prepared
    request = demo_request(p)
    call = dict(app_mode="normal")
    if case == "adapter_supplied":
        call["demo_adapter"] = p.provider
    elif case == "demo_fields":
        request = dataclasses.replace(
            OPENAI_REQUEST, model_id="demo-classroom-v1"
        )
        request = dataclasses.replace(
            request, demo_context=p.context, demo_selection=p.selection
        )
    events = await consume(
        permit(
            p.factory,
            provider="openai" if case == "demo_fields" else "demo",
        ),
        request,
        kind="text",
        **call,
    )
    assert [(e.type, e.error_code) for e in events] == [
        ("error", "configuration_unavailable")
    ]
    assert boundary.sdk == boundary.ledger == []
    assert p.visitors.calls == 0


def test_normal_default_mode_and_adapters_are_unchanged():
    import inspect

    parameters = inspect.signature(call_execution.execute_call).parameters
    for name in ("app_mode", "demo_adapter"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None
    assert set(call_execution.PROVIDER_ADAPTERS) == set(EXTERNAL)


SMOKE_GUARD_SCRIPT = """
import argparse
import builtins
import runpy
import sys
from unittest.mock import patch
import sqlalchemy.ext.asyncio

def forbidden(*args, **kwargs):
    raise AssertionError("CLI side effect before mode guard")
sys.argv = ["src.cli.provider_smoke", *{flags!r}]
with patch("builtins.open", forbidden), patch("pathlib.Path.open", forbidden), patch("pathlib.Path.read_text", forbidden), patch("sqlite3.connect", forbidden), patch("sqlalchemy.ext.asyncio.create_async_engine", forbidden), patch.object(argparse.ArgumentParser, "__init__", forbidden):
    try:
        runpy.run_module("src.cli.provider_smoke", run_name="__main__")
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("CLI did not exit")
assert "src.config" not in sys.modules
"""


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--execute"],
        ["--execute", "--approval", "/nonexistent/approval.json"],
    ],
)
@pytest.mark.parametrize("testing", ["true", None])
@pytest.mark.parametrize(
    "app_mode,code",
    [
        ("demo", "DEMO_ACTION_FORBIDDEN"),
        ("staging", "DEMO_CONFIGURATION_INVALID"),
    ],
)
def test_provider_smoke_blocks_non_normal_modes_before_side_effects(
    flags, testing, app_mode, code
):
    env = {"APP_MODE": app_mode}
    if testing:
        env["TESTING"] = testing
    result = run_python(SMOKE_GUARD_SCRIPT.format(flags=flags), env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"code": code, "exit_status": 1}


async def test_provider_smoke_in_process_demo_guard(
    monkeypatch, capsys, tmp_path
):
    def forbidden(*args, **kwargs):
        raise AssertionError("parsed before mode guard")

    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setattr(argparse.ArgumentParser, "__init__", forbidden)
    with pytest.raises(ValueError, match="DEMO_ACTION_FORBIDDEN"):
        await provider_smoke.run(
            tmp_path / "missing.db",
            [],
            owner_id=1,
            execute=True,
            approval={},
            app_mode="demo",
        )
    assert provider_smoke.main(app_mode="demo") == 1
    assert json.loads(capsys.readouterr().out) == {
        "code": "DEMO_ACTION_FORBIDDEN",
        "exit_status": 1,
    }


def test_provider_smoke_normal_cli_path_is_reachable():
    result = run_python(
        """
import argparse
import runpy
import sys
from unittest.mock import patch
class Reached(Exception):
    pass
def reached(*args, **kwargs):
    raise Reached()
sys.argv = ["src.cli.provider_smoke", "--help"]
with patch.object(argparse.ArgumentParser, "parse_args", reached):
    try:
        runpy.run_module("src.cli.provider_smoke", run_name="__main__")
    except Reached:
        pass
    else:
        raise AssertionError("normal CLI unreachable")
""",
        env={"APP_MODE": "normal", "TESTING": "true"},
    )
    assert result.returncode == 0, result.stderr

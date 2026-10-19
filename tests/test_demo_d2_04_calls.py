"""D2-04: demo request fields, calls, run ownership and CLI refusal."""

import argparse
import asyncio
import dataclasses
import json
import time
from contextlib import aclosing, asynccontextmanager, suppress
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from lesson_fixtures import install_snapshot
from sqlalchemy import func, select, update
from test_anthropic_invocations import REQUEST as ANTHROPIC_REQUEST
from test_demo_config import run_python
from test_demo_provider import prepared as demo_prepared
from test_google_invocations import REQUEST as GOOGLE_REQUEST
from test_openai_invocations import REQUEST as OPENAI_REQUEST

from src.api.routes.student_generation import (
    DEMO_ERRORS,
    demo_error_response,
)
from src.cli import provider_smoke
from src.demo.database import DemoError
from src.demo.lifecycle import Lifecycle, TaskControl
from src.demo.provider import DemoProvider, Selection
from src.demo.visitors import DemoContext
from src.models import (
    AppSetting,
    GenerationRun,
    Message,
    ModelConfig,
    ProviderConnection,
)
from src.models import Session as LessonSession
from src.services import (
    anthropic_catalog,
    anthropic_generation,
    call_admission,
    call_execution,
    context_budget,
    google_catalog,
    google_generation,
    mentor_stream,
    openai_catalog,
    openai_generation,
    student_stream,
    tutor_bot,
)
from src.services.call_admission import (
    CallPermit,
    active_calls,
    admit_call,
    approve_call,
    cancel_connection,
    recheck_call,
    registered_calls,
)
from src.services.generation_runs import (
    DemoBinding,
    finish_student,
    reserve_student,
    snapshot,
)
from src.services.invocation_types import (
    CallEvent,
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.lesson_snapshots import canonical_hash
from src.services.mentor_generation import reserve_mentor
from src.services.model_verification import (
    DEMO_SEED_TIME,
    ROLE_CONTRACT_VERSIONS,
)
from src.services.role_output_contracts import MentorOutput

prepared = demo_prepared


@pytest.fixture
def demo_server(monkeypatch):
    """Server mode is fixed at boot; unit tests swap the read config only."""
    server = SimpleNamespace(
        APP_MODE="demo", DEMO_MAX_ACTIVE_GLOBAL=8, DEMO_MAX_ACTIVE_VISITOR=2
    )
    monkeypatch.setattr(call_admission, "config", server)
    monkeypatch.setattr(call_execution, "config", server)
    return server


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


async def test_ledger_writes_carry_the_run_context_only_in_demo(
    prepared, boundary
):
    p = prepared
    registry = object()
    call_permit = permit(p.factory)
    call_permit.registry = registry
    request = demo_request(p)
    await consume(
        call_permit,
        request,
        kind="text",
        app_mode="demo",
        demo_adapter=p.provider,
    )
    for values in (boundary.ledger[0], boundary.finished[0]):
        assert values["ctx"] is request.demo_context
        assert values["registry"] is registry
    boundary.ledger.clear()
    boundary.finished.clear()
    normal = permit(p.factory, provider="openai")
    normal.model_id = OPENAI_REQUEST.model_id
    await consume(normal, OPENAI_REQUEST, kind="text")
    # Normal keeps the unguarded ledger call signature.
    for values in (boundary.ledger[0], boundary.finished[0]):
        assert "ctx" not in values and "registry" not in values


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
    # Operation names alone refuse comparisons; no scope label supplied.
    "comparison_operation": dict(operation="comparison"),
    "comparison_student": dict(operation="comparison_student"),
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
    assert boundary.rechecks == 0
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


@pytest.fixture
async def lifecycle(tmp_path):
    """Real #191 visitor authority and #192 registry over a migrated seed."""
    from test_demo_d1_01_database import make_root

    from src.demo.visitors import VisitorRegistry

    root = await make_root(tmp_path)
    registry = Lifecycle()
    registry.visitors = VisitorRegistry(root, lifecycle=registry)
    yield registry
    await registry.visitors.shutdown()
    root.close()


class RunningDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, key):
        return SimpleNamespace(status="running")


async def demo_run(lifecycle):
    """What the route does: register, then the request lease ends."""
    from test_demo_d1_01_visitors import ready_context

    visitors = lifecycle.visitors
    _, _, ctx = await ready_context(visitors, "teacher")
    run_id = str(uuid4())
    cancelled = asyncio.Event()
    token = await lifecycle.register(
        ctx, "run", run_id, TaskControl(cancelled.set, asyncio.Event())
    )
    await visitors.release_lease(ctx.lease)
    binding = DemoBinding(
        dataclasses.replace(ctx, lease=token.registry_lease),
        lifecycle,
        None,
        {},
        None,
    )
    return student_stream.DemoRun(binding, token, cancelled), run_id


def recorder(saved):
    async def finish(factory, run_id, *, status, demo=None, **values):
        # A real final save holds the run's write guard.
        async with demo.guard():
            saved.append((status, values))
        return dict(
            status=status,
            error_code=values.get("error_code"),
            retryable=status in ("failed", "interrupted"),
            message={"content": values.get("content")},
            result_kind="message",
            turn_index=1,
        )

    return finish


async def serve(response):
    sent = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await response(
        {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
    )
    body = b"".join(m.get("body", b"") for m in sent).decode()
    return [
        (frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:]))
        for frame in body.split("\n\n")
        if frame.startswith("event:")
    ]


def accepted(run_id):
    return dict(
        run_id=run_id,
        turn_id=str(uuid4()),
        request_id=str(uuid4()),
        turn_index=1,
        teacher_message_id=1,
        session_id=2,
    )


def fake_permit():
    return SimpleNamespace(release=lambda: None, task=None)


async def test_demo_student_run_owns_lease_until_saved_and_popped(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    visitors = lifecycle.visitors
    saved, seen = [], {}
    adapter = object()

    async def execute_call(permit, request, **kwargs):
        seen["adapter"] = kwargs["demo_adapter"]
        # Registry lease plus the producer's own child lease.
        seen["leases"] = visitors.lease_count(scope)
        yield CallEvent("text_delta", text="Hi")
        yield CallEvent("completed", text="Hi")

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    frames = await serve(
        student_stream.StudentStreamingResponse(
            RunningDB,
            accepted(run_id),
            dict(permit=fake_permit(), request=None, demo_adapter=adapter),
            finisher=recorder(saved),
            demo_run=run,
        )
    )
    ctx = run.binding.ctx
    assert [kind for kind, _ in frames] == [
        "run.accepted",
        "output.delta",
        "output.completed",
    ]
    for _, data in frames:
        assert (
            data["generation"],
            data["auth_epoch"],
            data["browser_scope_id"],
        ) == (ctx.generation, ctx.auth_epoch, ctx.browser_scope_id)
        assert "recovery" not in data
    assert [(status, v["content"]) for status, v in saved] == [
        ("completed", "Hi")
    ]
    assert seen == {"adapter": adapter, "leases": 2}
    assert run.token.completed
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert visitors.lease_count(scope) == 0


async def test_revoked_context_gets_only_a_body_free_terminal(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    visitors = lifecycle.visitors
    saved = []

    async def execute_call(permit, request, **kwargs):
        async with visitors.transition(
            run.binding.ctx.verified_visitor
        ) as handle:
            handle.mark_reset(run.binding.ctx, str(uuid4()))
        yield CallEvent("text_delta", text="hidden body")
        yield CallEvent("completed", text="hidden body")

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    frames = await serve(
        student_stream.StudentStreamingResponse(
            RunningDB,
            accepted(run_id),
            dict(permit=fake_permit(), request=None),
            finisher=recorder(saved),
            demo_run=run,
        )
    )
    assert [kind for kind, _ in frames] == ["run.accepted", "run.cancelled"]
    terminal = frames[-1][1]
    assert terminal["code"] == "DEMO_STALE_GENERATION"
    assert terminal["recovery"] == "demo.stale_generation"
    assert terminal["retryable"] is False
    assert "hidden body" not in json.dumps(frames, ensure_ascii=False)
    assert saved == []
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert visitors.lease_count(scope) == 0


async def test_scoped_cancel_ends_the_run_through_its_task_control(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    saved = []
    started = asyncio.Event()

    async def execute_call(permit, request, **kwargs):
        started.set()
        await asyncio.Event().wait()
        yield CallEvent("completed", text="never")

    async def cancel():
        await started.wait()
        assert lifecycle.cancel(("normal", 0), "run", run_id) == 0
        assert lifecycle.cancel(scope, "run", run_id) == 1

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    canceller = asyncio.create_task(cancel())
    frames = await serve(
        student_stream.StudentStreamingResponse(
            RunningDB,
            accepted(run_id),
            dict(permit=fake_permit(), request=None),
            finisher=recorder(saved),
            demo_run=run,
        )
    )
    await canceller
    assert frames[-1][0] == "run.cancelled"
    assert frames[-1][1]["code"] == "session_ended"
    assert frames[-1][1]["recovery"] == "demo.stopped"
    assert [(status, v["error_code"]) for status, v in saved] == [
        ("cancelled", "session_ended")
    ]
    assert lifecycle.lookup(scope, "run", run_id) is None


async def test_demo_mentor_run_saves_under_guard_and_pops(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    visitors = lifecycle.visitors
    saved, seen = [], {}
    adapter = object()

    async def execute_call(permit, request, **kwargs):
        seen["adapter"] = kwargs["demo_adapter"]
        seen["leases"] = visitors.lease_count(scope)
        yield CallEvent(
            "completed",
            structured=dict(feedback="coach", reason_summary="why"),
        )

    monkeypatch.setattr(tutor_bot, "execute_call", execute_call)
    monkeypatch.setattr(mentor_stream, "finish_mentor", recorder(saved))
    response = mentor_stream.mentor_response(
        RunningDB,
        accepted(run_id),
        dict(
            permit=fake_permit(),
            request=None,
            deadline=None,
            demo_adapter=adapter,
        ),
        demo_run=run,
    )
    response.finisher = recorder(saved)
    frames = await serve(response)
    assert [kind for kind, _ in frames] == [
        "run.accepted",
        "output.completed",
    ]
    assert frames[-1][1]["generation"] == run.binding.ctx.generation
    assert saved == [
        ("completed", {"content": "coach", "reason_summary": "why"})
    ]
    assert seen == {"adapter": adapter, "leases": 2}
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert visitors.lease_count(scope) == 0


S2_SETTING = SimpleNamespace(
    limits_json=dict(
        total=10, openai=4, anthropic=4, google=4, admin=1, demo=4
    ),
    timeouts_json=dict(
        connect=5,
        student_first_output=60,
        student_total=180,
        mentor_first_output=60,
        mentor_total=180,
        analysis_total=300,
        model_list_total=30,
    ),
)


def s2_connection(provider, encrypted_key=None, connection_id=1):
    return SimpleNamespace(
        id=connection_id,
        provider=provider,
        enabled=True,
        credential_revision=1,
        connection_version=1,
        encrypted_key=encrypted_key,
    )


def s2_context(factory=None):
    return DemoContext(
        visitor_id=str(uuid4()),
        browser_scope_id=str(uuid4()),
        generation=1,
        auth_epoch=1,
        role="teacher",
        user_id=1,
        created_at=None,
        last_activity_at=None,
        idle_expires_at=None,
        absolute_expires_at=None,
        expires_at=None,
        factory=factory if factory is not None else object(),
        lease=SimpleNamespace(released=False),
        verified_visitor=object(),
    )


class S2Registry:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def assert_current(self, ctx):
        self.calls.append(ctx)
        if self.error is not None:
            raise self.error


@pytest.fixture
def s2_decrypt(monkeypatch):
    calls = []

    def decrypt(connection):
        calls.append(connection)
        return "secret"

    monkeypatch.setattr(call_admission, "decrypt_key", decrypt)
    yield calls
    call_admission.active_calls.clear()
    call_admission.registered_calls.clear()


def s2_approve(connection, ctx=None, registry=None, factory=None, **kw):
    if factory is None:
        factory = ctx.factory if ctx is not None else object()
    options = dict(owner_id=1, operation="student", role="student")
    options.update(kw, admin=kw.get("admin", False))
    return approve_call(
        factory,
        connection,
        S2_SETTING,
        ctx=ctx,
        registry=registry,
        **options,
    )


async def test_s2_normal_openai_decrypts_and_has_no_demo_state(s2_decrypt):
    permit = s2_approve(s2_connection("openai", b"x"))
    assert s2_decrypt and len(s2_decrypt) == 1
    assert permit.secret == "secret"
    assert permit.ctx is None and permit.registry is None
    assert permit.scope == ("normal", 0)
    permit.release()


async def test_s2_normal_rejects_demo_provider(s2_decrypt):
    with pytest.raises(InvocationError) as error:
        s2_approve(s2_connection("demo"))
    assert error.value.code == "configuration_unavailable"
    assert s2_decrypt == []
    assert not call_admission.active_calls


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_s2_demo_rejects_real_provider_without_decrypting(
    demo_server, s2_decrypt, provider
):
    ctx = s2_context()
    with pytest.raises(InvocationError) as error:
        s2_approve(s2_connection(provider, b"x"), ctx, S2Registry())
    assert error.value.code == "configuration_unavailable"
    assert s2_decrypt == []
    assert not call_admission.active_calls


@pytest.mark.parametrize("operation", ["model_list", "probe"])
async def test_s2_demo_rejects_provider_only_operations(
    demo_server, s2_decrypt, operation
):
    ctx = s2_context()
    with pytest.raises(InvocationError):
        s2_approve(
            s2_connection("demo"), ctx, S2Registry(), operation=operation
        )
    assert s2_decrypt == []


async def test_s2_demo_rejects_stored_key(demo_server, s2_decrypt):
    ctx = s2_context()
    with pytest.raises(InvocationError):
        s2_approve(s2_connection("demo", b"x"), ctx, S2Registry())
    assert s2_decrypt == []


async def test_s2_demo_requires_registry(demo_server, s2_decrypt):
    ctx = s2_context()
    with pytest.raises(InvocationError):
        s2_approve(s2_connection("demo"), ctx, None)


async def test_s2_demo_rejects_foreign_factory(demo_server, s2_decrypt):
    ctx = s2_context()
    with pytest.raises(InvocationError):
        s2_approve(s2_connection("demo"), ctx, S2Registry(), factory=object())
    assert not call_admission.active_calls


async def test_s2_demo_valid_permit_carries_context(demo_server, s2_decrypt):
    ctx, registry = s2_context(), S2Registry()
    permit = s2_approve(s2_connection("demo"), ctx, registry)
    assert permit.secret is None
    assert s2_decrypt == []
    assert permit.ctx is ctx and permit.registry is registry
    assert permit.scope == (ctx.visitor_id, ctx.generation)
    assert permit in call_admission.active_calls
    permit.release()
    assert permit not in call_admission.registered_calls


async def test_s2_cancel_connection_never_crosses_scopes(
    monkeypatch, demo_server, s2_decrypt
):
    ctx_a, ctx_b = s2_context(), s2_context()
    permits = {
        "a": s2_approve(s2_connection("demo"), ctx_a, S2Registry()),
        "b": s2_approve(s2_connection("demo"), ctx_b, S2Registry()),
    }
    # The one normal permit is admitted while the server is in normal mode.
    monkeypatch.setattr(
        call_admission, "config", SimpleNamespace(APP_MODE="normal")
    )
    permits["n"] = s2_approve(s2_connection("openai", b"x"))
    monkeypatch.setattr(call_admission, "config", demo_server)
    tasks = {k: asyncio.create_task(asyncio.sleep(60)) for k in permits}
    for key, permit in permits.items():
        permit.task = tasks[key]
    try:
        cancel_connection(1, scope=permits["a"].scope)
        await asyncio.sleep(0)
        assert tasks["a"].cancelling() == 1
        assert tasks["b"].cancelling() == 0 and tasks["n"].cancelling() == 0
        cancel_connection(1)
        await asyncio.sleep(0)
        assert tasks["n"].cancelling() == 1
        assert tasks["b"].cancelling() == 0
        cancel_connection(2, scope=permits["b"].scope)
        await asyncio.sleep(0)
        assert tasks["b"].cancelling() == 0
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for permit in permits.values():
            permit.release()


async def s2_demo_row(data):
    # ck_provider_key_material pins demo rows to revision 0 and no key.
    row = ProviderConnection(provider="demo", enabled=True)
    data.db.add(row)
    await data.db.commit()
    return row


class S2KeyedFactory:
    """The schema forbids a keyed demo row, so serve one from memory."""

    def __init__(self, connection):
        self.connection = connection

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, key):
        return self.connection


async def test_s2_recheck_demo_asserts_current_then_passes(
    data, demo_server, s2_decrypt
):
    row = await s2_demo_row(data)
    ctx, registry = s2_context(data.factory), S2Registry()
    permit = s2_approve(row, ctx, registry)
    await recheck_call(permit)
    assert registry.calls == [ctx]
    permit.release()


async def test_s2_recheck_demo_stale_generation_stops(
    data, demo_server, s2_decrypt
):
    row = await s2_demo_row(data)
    registry = S2Registry(DemoError("STALE_GENERATION"))
    permit = s2_approve(row, s2_context(data.factory), registry)
    with pytest.raises(DemoError) as error:
        await recheck_call(permit)
    assert error.value.code == "DEMO_STALE_GENERATION"
    assert len(registry.calls) == 1
    permit.release()


async def test_s2_recheck_demo_refuses_stored_key(demo_server, s2_decrypt):
    keyed = S2KeyedFactory(s2_connection("demo", b"x"))
    ctx = s2_context(keyed)
    permit = s2_approve(s2_connection("demo"), ctx, S2Registry())
    with pytest.raises(InvocationError) as error:
        await recheck_call(permit)
    assert error.value.code == "configuration_unavailable"
    permit.release()


async def test_s2_recheck_normal_permit_refuses_demo_connection(
    data, s2_decrypt
):
    row = await s2_demo_row(data)
    permit = s2_approve(
        s2_connection("openai", b"x", row.id), factory=data.factory
    )
    with pytest.raises(InvocationError) as error:
        await recheck_call(permit)
    assert error.value.code == "configuration_unavailable"
    permit.release()


def s2_demo_request(cls=TextRequest, **extra):
    return cls(
        "demo",
        "demo-classroom-v1",
        "student",
        "sys",
        [{"role": "user", "content": "hi"}],
        {"max_output_tokens": 4096},
        "r",
        **extra,
    )


@pytest.fixture
def s2_no_provider_parameters(monkeypatch):
    def forbidden(request):
        raise AssertionError("provider_parameters must not be called")

    monkeypatch.setattr(context_budget, "provider_parameters", forbidden)


def test_s2_demo_text_budget_without_provider_parameters(
    s2_no_provider_parameters,
):
    fitted = context_budget.fit_context(
        s2_demo_request(), prior_pair_count=0, configured_prior_turn_limit=None
    )
    budget = fitted.context_budget_json
    assert budget["reserved_output_tokens"] == 4096
    assert budget["input_budget_tokens"] == 65536
    assert budget["capability_definition_version"] == "demo-cap-v1"


def test_s2_demo_structured_budget_without_provider_parameters(
    s2_no_provider_parameters,
):
    request = s2_demo_request(StructuredRequest, output_schema=MentorOutput)
    request = replace(
        request, role="mentor", validated_options={"max_output_tokens": 8192}
    )
    fitted = context_budget.fit_context(
        request, prior_pair_count=0, configured_prior_turn_limit=None
    )
    assert fitted.context_budget_json["reserved_output_tokens"] == 8192


def test_s2_openai_budget_still_uses_provider_parameters(monkeypatch):
    calls = []

    def parameters(request):
        calls.append(request)
        return {"max_output_tokens": 1024}

    monkeypatch.setattr(context_budget, "provider_parameters", parameters)
    fitted = context_budget.fit_context(
        OPENAI_REQUEST, prior_pair_count=0, configured_prior_turn_limit=None
    )
    assert calls
    assert fitted.context_budget_json["reserved_output_tokens"] == 1024


S2R_HEX = "ab" * 32
S2R_OTHER_HEX = "cd" * 32


class S2rRegistry:
    """write_guard records enter/exit and can refuse like a stale visitor."""

    def __init__(self):
        self.entered = 0
        self.exited = 0
        self.stale = False
        self.visitors = object()

    @asynccontextmanager
    async def write_guard(self, ctx):
        if self.stale:
            raise DemoError("STALE_GENERATION")
        self.entered += 1
        try:
            yield
        finally:
            self.exited += 1

    async def assert_current(self, ctx):
        return None


class S2rHash:
    """Stub for #195's helper: returns the manifest hex unless overridden."""

    def __init__(self):
        self.value = S2R_HEX
        self.calls = []

    def __call__(self, schema_version, config):
        self.calls.append((schema_version, config))
        return self.value


@pytest.fixture
async def s2r_demo(data, tmp_path, monkeypatch, demo_server):
    db = data.db
    connection = ProviderConnection(
        provider="demo",
        enabled=True,
        credential_revision=0,
        connection_version=1,
    )
    db.add(connection)
    await db.flush()
    roles = ("student", "mentor", "analysis")
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id="demo-classroom-v1",
        display_name="Demo classroom",
        enabled=True,
        capability_definition_version="demo-cap-v1",
        default_options_json={"max_output_tokens": 4096},
        verification_state={
            role: {
                "status": "succeeded",
                "verified_at": DEMO_SEED_TIME.isoformat(),
                "credential_revision": 0,
                "connection_version": 1,
                "capability_definition_version": "demo-cap-v1",
                "role_contract_version": ROLE_CONTRACT_VERSIONS[role],
            }
            for role in roles
        },
    )
    db.add(model)
    if await db.get(AppSetting, 1) is None:
        db.add(
            AppSetting(
                id=1,
                limits_json=dict(
                    total=8, openai=4, anthropic=4, google=4, admin=3, demo=4
                ),
                timeouts_json=dict(
                    connect=5,
                    student_first_output=60,
                    student_total=180,
                    mentor_first_output=60,
                    mentor_total=180,
                    analysis_total=300,
                    model_list_total=30,
                ),
            )
        )
    await db.commit()
    await install_snapshot(
        data, connection, model, options={"max_output_tokens": 4096}
    )
    envelope = deepcopy(data.session.config_snapshot_json)
    envelope["config"]["mentor"]["mode"] = "manual"
    envelope["config"]["mentor"]["resolved_model_config"] = deepcopy(
        envelope["config"]["student"]["resolved_model_config"]
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    await db.commit()

    manifest = Path(tmp_path / "manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "supported_config_sha256": S2R_HEX,
                # Provenance compares the run's session with this one.
                "seed_refs": {"completed_session": 987654},
            }
        )
    )
    seed = SimpleNamespace(
        manifest_path=manifest,
        db_schema_version="x",
        migration_set_sha256="a" * 64,
    )
    inputs = dict(
        A="허구 질문 가나다",
        B="허구 질문 라마바",
        C="허구 질문 사아자",
        common="허구 공통 질문",
    )
    ctx = DemoContext(
        visitor_id=str(uuid4()),
        browser_scope_id=str(uuid4()),
        generation=1,
        auth_epoch=1,
        role="teacher",
        user_id=data.owner.id,
        created_at=None,
        last_activity_at=None,
        idle_expires_at=None,
        absolute_expires_at=None,
        expires_at=None,
        factory=data.factory,
        lease=SimpleNamespace(released=False),
        verified_visitor=object(),
    )
    registry = S2rRegistry()
    stub = S2rHash()
    binding = DemoBinding(ctx, registry, seed, inputs, stub)
    decrypted = []
    monkeypatch.setattr(
        call_admission, "decrypt_key", lambda c: decrypted.append(c)
    )
    before = (set(active_calls), set(registered_calls))
    yield SimpleNamespace(
        data=data,
        ctx=ctx,
        registry=registry,
        stub=stub,
        binding=binding,
        inputs=inputs,
        seed=seed,
        decrypted=decrypted,
    )
    active_calls.intersection_update(before[0])
    registered_calls.intersection_update(before[1])


def s2r_request(content, request_id=None):
    return SimpleNamespace(
        request_id=request_id or str(uuid4()), content=content, turn_id=None
    )


async def s2r_reserve(demo, content, request_id=None, run_id=None):
    return await reserve_student(
        demo.data.factory,
        demo.data.session.id,
        demo.data.owner,
        s2r_request(content, request_id),
        demo=demo.binding,
        run_id=run_id,
    )


async def s2r_counts(demo):
    async with demo.data.factory() as db:
        teachers = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.role == "teacher")
        )
        runs = await db.scalar(select(func.count()).select_from(GenerationRun))
        questions = await db.scalar(
            select(LessonSession.tutor_question_count).where(
                LessonSession.id == demo.data.session.id
            )
        )
    return teachers, runs, questions


async def s2r_run_status(demo, run_id):
    async with demo.data.factory() as db:
        return (await db.get(GenerationRun, run_id)).status


async def test_s2r_student_reservation_binds_demo_selection(s2r_demo):
    demo = s2r_demo
    run_id = str(uuid4())
    accepted, execution = await s2r_reserve(
        demo, demo.inputs["A"], None, run_id
    )
    try:
        permit = execution["permit"]
        assert permit.secret is None
        assert permit.ctx is demo.ctx
        assert permit.registry is demo.registry
        request = execution["request"]
        assert request.demo_context is demo.ctx
        selection = request.demo_selection
        assert isinstance(selection, Selection)
        assert (selection.role, selection.content_state) == (
            "student",
            "initial",
        )
        assert (selection.branch, selection.input_kind) == ("A", "A")
        assert len(selection.manifest_sha256) == 64
        assert accepted["run_id"] == run_id
        assert isinstance(execution["demo_adapter"], DemoProvider)
        assert (demo.registry.entered, demo.registry.exited) == (1, 1)
        assert demo.decrypted == []
        assert await s2r_counts(demo) == (1, 1, 0)
        assert await s2r_run_status(demo, run_id) == "running"
    finally:
        execution["permit"].release()


async def test_s2r_unsupported_input_text_selects_unsupported(s2r_demo):
    _, execution = await s2r_reserve(s2r_demo, "허구 미지원 문장")
    try:
        selection = execution["request"].demo_selection
        assert selection.input_kind == "unsupported"
        assert selection.branch is None
        assert selection.content_state == "initial"
    finally:
        execution["permit"].release()


async def test_s2r_replay_returns_snapshot_and_conflict_is_demo_error(s2r_demo):
    demo = s2r_demo
    request_id = str(uuid4())
    run_id = str(uuid4())
    first, execution = await s2r_reserve(
        demo, demo.inputs["A"], request_id, run_id
    )
    execution["permit"].release()
    second, again = await s2r_reserve(demo, demo.inputs["A"], request_id)
    assert again is None
    assert second["run_id"] == first["run_id"] == run_id
    assert await s2r_counts(demo) == (1, 1, 0)
    with pytest.raises(DemoError) as caught:
        await s2r_reserve(demo, demo.inputs["B"], request_id)
    assert caught.value.code == "DEMO_REQUEST_CONFLICT"
    assert await s2r_counts(demo) == (1, 1, 0)


async def test_s2r_unsupported_configuration_leaves_no_rows(s2r_demo):
    demo = s2r_demo
    demo.stub.value = S2R_OTHER_HEX
    with pytest.raises(DemoError) as caught:
        await s2r_reserve(demo, demo.inputs["A"])
    assert caught.value.code == "DEMO_UNSUPPORTED_CONFIGURATION"
    assert caught.value.status_code == 422
    assert (caught.value.generation, caught.value.auth_epoch) == (1, 1)
    assert await s2r_counts(demo) == (0, 0, 0)
    assert demo.decrypted == []
    assert demo.stub.calls
    assert (demo.registry.entered, demo.registry.exited) == (1, 1)
    assert not any(p.ctx is demo.ctx for p in active_calls)


async def test_s2r_guard_refusal_writes_nothing(s2r_demo):
    demo = s2r_demo
    demo.registry.stale = True
    with pytest.raises(DemoError) as caught:
        await s2r_reserve(demo, demo.inputs["A"])
    assert caught.value.code == "DEMO_STALE_GENERATION"
    assert await s2r_counts(demo) == (0, 0, 0)
    assert demo.stub.calls == []
    assert not any(p.ctx is demo.ctx for p in active_calls)


async def test_s2r_finish_student_runs_inside_the_guard(s2r_demo):
    demo = s2r_demo
    run_id = str(uuid4())
    _, execution = await s2r_reserve(demo, demo.inputs["A"], None, run_id)
    execution["permit"].release()
    result = await finish_student(
        demo.data.factory,
        run_id,
        status="completed",
        content="허구 응답",
        demo=demo.binding,
    )
    assert result["status"] == "completed"
    assert result["message"]["content"] == "허구 응답"
    assert (demo.registry.entered, demo.registry.exited) == (2, 2)
    async with demo.data.factory() as db:
        student = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.role == "student")
        )
    assert student == 1


async def test_s2r_finish_student_stale_guard_keeps_run_running(s2r_demo):
    demo = s2r_demo
    run_id = str(uuid4())
    _, execution = await s2r_reserve(demo, demo.inputs["A"], None, run_id)
    execution["permit"].release()
    demo.registry.stale = True
    with pytest.raises(DemoError) as caught:
        await finish_student(
            demo.data.factory,
            run_id,
            status="completed",
            content="x",
            demo=demo.binding,
        )
    assert caught.value.code == "DEMO_STALE_GENERATION"
    assert await s2r_run_status(demo, run_id) == "running"
    async with demo.data.factory() as db:
        student = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.role == "student")
        )
    assert student == 0


async def s2r_completed_turn(demo):
    run_id = str(uuid4())
    accepted, execution = await s2r_reserve(
        demo, demo.inputs["A"], None, run_id
    )
    execution["permit"].release()
    await finish_student(
        demo.data.factory,
        run_id,
        status="completed",
        content="허구 응답",
        demo=demo.binding,
    )
    return accepted["turn_id"]


async def s2r_mentor(demo, turn_id, run_id=None):
    return await reserve_mentor(
        demo.data.factory,
        demo.data.session.id,
        turn_id,
        demo.data.owner,
        str(uuid4()),
        "manual",
        demo=demo.binding,
        run_id=run_id,
    )


async def test_s2r_mentor_reservation_binds_demo_selection(s2r_demo):
    demo = s2r_demo
    turn_id = await s2r_completed_turn(demo)
    entered = demo.registry.entered
    run_id = str(uuid4())
    accepted, execution = await s2r_mentor(demo, turn_id, run_id)
    try:
        assert accepted["run_id"] == run_id
        assert execution["permit"].secret is None
        assert execution["permit"].ctx is demo.ctx
        request = execution["request"]
        assert request.demo_context is demo.ctx
        selection = request.demo_selection
        assert isinstance(selection, Selection)
        assert selection.role == "mentor"
        assert selection.trigger == "manual"
        assert selection.target_turn_id == turn_id
        assert selection.content_state == "after_first"
        assert (selection.branch, selection.input_kind) == ("A", "A")
        assert isinstance(execution["demo_adapter"], DemoProvider)
        assert demo.registry.entered == entered + 1
        assert demo.registry.exited == entered + 1
        assert demo.decrypted == []
        assert (await s2r_counts(demo))[2] == 1
    finally:
        execution["permit"].release()


async def test_s2r_mentor_unsupported_configuration_changes_nothing(s2r_demo):
    demo = s2r_demo
    turn_id = await s2r_completed_turn(demo)
    before = await s2r_counts(demo)
    demo.stub.value = S2R_OTHER_HEX
    with pytest.raises(DemoError) as caught:
        await s2r_mentor(demo, turn_id)
    assert caught.value.code == "DEMO_UNSUPPORTED_CONFIGURATION"
    assert await s2r_counts(demo) == before
    async with demo.data.factory() as db:
        mentors = await db.scalar(
            select(func.count())
            .select_from(GenerationRun)
            .where(GenerationRun.operation == "mentor")
        )
    assert mentors == 0
    assert demo.decrypted == []


# S2R1-A admission
def s2a_setting(**limits):
    base = dict(S2_SETTING.limits_json, **limits)
    return SimpleNamespace(
        limits_json=base, timeouts_json=S2_SETTING.timeouts_json
    )


def s2a_demo_approve(ctx, setting=S2_SETTING, registry=None, **kw):
    return approve_call(
        ctx.factory,
        s2_connection("demo"),
        setting,
        owner_id=1,
        operation=kw.pop("operation", "student"),
        role="student",
        admin=False,
        ctx=ctx,
        registry=registry or S2Registry(),
    )


def s2a_boom():
    raise AssertionError("factory opened")


def s2a_refused(call):
    with pytest.raises(InvocationError) as error:
        call()
    assert error.value.code == "configuration_unavailable"


@pytest.mark.parametrize("case", ["context", "registry", "provider"])
async def test_s2a_normal_server_refuses_demo_input_in_approve(
    s2_decrypt, case
):
    ctx = s2_context()
    kw = dict(ctx=ctx, registry=S2Registry())
    connection = s2_connection("openai", b"x")
    if case == "registry":
        kw = dict(registry=S2Registry())
    elif case == "provider":
        kw, connection = {}, s2_connection("demo")
    s2a_refused(lambda: s2_approve(connection, factory=ctx.factory, **kw))
    assert s2_decrypt == [] and not active_calls


async def test_s2a_normal_server_admit_refuses_before_any_db(s2_decrypt):
    registry = S2Registry()
    ctx = s2_context(s2a_boom)
    with pytest.raises(InvocationError) as error:
        await admit_call(
            s2a_boom,
            connection_id=1,
            owner_id=1,
            operation="student",
            ctx=ctx,
            registry=registry,
        )
    assert error.value.code == "configuration_unavailable"
    assert registry.calls == [] and not active_calls


async def test_s2a_normal_server_recheck_refuses_permit_with_context(
    s2_decrypt,
):
    value = permit(s2a_boom, operation="student")
    value.ctx = s2_context(s2a_boom)
    with pytest.raises(InvocationError) as error:
        await recheck_call(value)
    assert error.value.code == "configuration_unavailable"
    value.release()


async def test_s2a_normal_server_never_executes_a_demo_permit(
    prepared, boundary
):
    p = prepared
    value = permit(p.factory)
    value.ctx = p.context
    events = await consume(
        value, demo_request(p), kind="text", app_mode="normal"
    )
    assert [(e.type, e.error_code) for e in events] == [
        ("error", "configuration_unavailable")
    ]
    assert boundary.sdk == boundary.ledger == []
    assert boundary.rechecks == 0
    value.release()


S2A_FORBIDDEN = [
    "comparison",
    "comparison_student",
    "comparison_mentor",
    "model_list",
    "probe",
]


@pytest.mark.parametrize("operation", S2A_FORBIDDEN)
async def test_s2a_demo_refuses_forbidden_operations(
    demo_server, s2_decrypt, operation
):
    ctx, registry = s2_context(s2a_boom), S2Registry()
    s2a_refused(
        lambda: s2a_demo_approve(ctx, registry=registry, operation=operation)
    )
    assert not active_calls
    with pytest.raises(InvocationError) as error:
        await admit_call(
            s2a_boom,
            connection_id=1,
            owner_id=1,
            operation=operation,
            ctx=ctx,
            registry=registry,
        )
    assert error.value.code == "configuration_unavailable"
    assert registry.calls == [] and not active_calls
    value = permit(s2a_boom, operation=operation)
    value.ctx, value.registry = ctx, registry
    with pytest.raises(InvocationError):
        await recheck_call(value)
    assert registry.calls == []
    value.release()


def s2a_stale(case):
    ctx, registry = s2_context(s2a_boom), S2Registry()
    if case == "released":
        ctx.lease.released = True
    elif case == "factory":
        ctx = dataclasses.replace(ctx, factory=object())
    else:
        registry = S2Registry(DemoError("STALE_ROLE"))
    return ctx, registry


@pytest.mark.parametrize("case", ["released", "factory", "stale_role"])
async def test_s2a_demo_admit_checks_currentness_before_db_or_slot(
    demo_server, s2_decrypt, case
):
    ctx, registry = s2a_stale(case)
    before = set(active_calls)
    with pytest.raises((InvocationError, DemoError)) as error:
        await admit_call(
            s2a_boom,
            connection_id=1,
            owner_id=1,
            operation="student",
            ctx=ctx,
            registry=registry,
        )
    assert error.value.code in ("configuration_unavailable", "DEMO_STALE_ROLE")
    assert registry.calls == ([ctx] if case == "stale_role" else [])
    assert active_calls == before


async def test_s2a_demo_approve_rejects_a_released_lease(
    demo_server, s2_decrypt
):
    ctx = s2_context()
    ctx.lease.released = True
    s2a_refused(lambda: s2a_demo_approve(ctx))
    assert not active_calls


def s2a_limited(ctx, count, setting):
    return [s2a_demo_approve(ctx, setting) for _ in range(count)]


async def test_s2a_db_limits_count_only_the_permit_scope(
    demo_server, s2_decrypt
):
    demo_server.DEMO_MAX_ACTIVE_VISITOR = 99
    setting = s2a_setting(total=2, demo=2, admin=1)
    ctx_a, ctx_b = s2_context(), s2_context()
    s2a_limited(ctx_a, 2, setting)
    # The seed limit is full for visitor A only.
    assert s2a_demo_approve(ctx_b, setting).scope == (ctx_b.visitor_id, 1)
    with pytest.raises(InvocationError) as error:
        s2a_demo_approve(ctx_a, setting)
    assert error.value.code == "call_limit_reached"
    assert len(active_calls) == 3


async def test_s2a_visitor_cap_beats_large_db_limits(demo_server, s2_decrypt):
    setting = s2a_setting(total=50, demo=50)
    ctx = s2_context()
    s2a_limited(ctx, 2, setting)
    with pytest.raises(InvocationError) as error:
        s2a_demo_approve(ctx, setting)
    assert error.value.code == "call_limit_reached"
    assert len(active_calls) == 2


async def test_s2a_visitor_cap_counts_every_generation(demo_server, s2_decrypt):
    setting = s2a_setting(total=50, demo=50)
    first = s2_context()
    second = dataclasses.replace(first, generation=2)
    s2a_demo_approve(first, setting)
    s2a_demo_approve(second, setting)
    for ctx in (first, second):
        with pytest.raises(InvocationError) as error:
            s2a_demo_approve(ctx, setting)
        assert error.value.code == "call_limit_reached"


async def test_s2a_global_cap_spans_visitors(demo_server, s2_decrypt):
    demo_server.DEMO_MAX_ACTIVE_GLOBAL = 3
    setting = s2a_setting(total=50, demo=50)
    ctx_a, ctx_b, ctx_c = s2_context(), s2_context(), s2_context()
    s2a_limited(ctx_a, 2, setting)
    s2a_demo_approve(ctx_b, setting)
    with pytest.raises(InvocationError) as error:
        s2a_demo_approve(ctx_c, setting)
    assert error.value.code == "call_limit_reached"
    assert len(active_calls) == 3


@dataclasses.dataclass(frozen=True)
class S2r1bRequest:
    demo_context: object = None


def s2r1b_permit(ctx=None):
    return SimpleNamespace(release=lambda: None, task=None, ctx=ctx)


def s2r1b_student(run, run_id, saved, db=RunningDB, **kwargs):
    kwargs.setdefault("permit", fake_permit())
    kwargs.setdefault("request", None)
    return student_stream.StudentStreamingResponse(
        db, accepted(run_id), kwargs, finisher=recorder(saved), demo_run=run
    )


def s2r1b_mentor(run, run_id, saved, db=RunningDB, **kwargs):
    kwargs.setdefault("permit", fake_permit())
    kwargs.setdefault("request", None)
    response = mentor_stream.mentor_response(
        db, accepted(run_id), dict(deadline=None, **kwargs), demo_run=run
    )
    response.finisher = recorder(saved)
    return response


async def s2r1b_revoke(run):
    visitors = run.binding.registry.visitors
    async with visitors.transition(run.binding.ctx.verified_visitor) as handle:
        handle.mark_reset(run.binding.ctx, str(uuid4()))


def s2r1b_done_db(run, status="completed"):
    """A run that ends between acceptance and the snapshot, then a reset."""

    class DB(RunningDB):
        async def get(self, model, key):
            await s2r1b_revoke(run)
            return SimpleNamespace(status=status)

    return DB


async def s2r1b_snapshot(db, run, **kwargs):
    return dict(status="completed", message="cached", turn_index=1)


def s2r1b_with_provenance(finish, provenance):
    async def wrapped(*args, **kwargs):
        result = await finish(*args, **kwargs)
        return (
            result
            if provenance is None
            else {**result, "provenance": provenance}
        )

    return wrapped


async def test_s2r1b_demo_abort_ends_an_unsent_response_once(lifecycle):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    saved = []
    permit = CallPermit(
        None,
        1,
        "demo",
        0,
        1,
        "student",
        "student",
        False,
        {},
        None,
        0.0,
        None,
        1,
    )
    active_calls.add(permit)
    registered_calls.add(permit)
    try:
        response = s2r1b_student(run, run_id, saved, permit=permit)
        assert lifecycle.lookup(scope, "run", run_id) is not None
        await response.demo_abort()
        await response.demo_abort()
        assert saved == [("interrupted", {"error_code": "disconnected"})]
        assert permit not in active_calls
        assert permit not in registered_calls
        assert lifecycle.lookup(scope, "run", run_id) is None
        assert run.token.task_control.done.is_set()
        assert lifecycle.visitors.lease_count(scope) == 0
    finally:
        permit.release()


def slot_permit():
    permit = CallPermit(
        None,
        1,
        "demo",
        0,
        1,
        "student",
        "student",
        False,
        {},
        None,
        0.0,
        None,
        1,
    )
    active_calls.add(permit)
    registered_calls.add(permit)
    return permit


async def test_cancelling_an_abort_never_skips_its_cleanup(lifecycle):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    entered, finish = asyncio.Event(), asyncio.Event()
    saved = []

    async def finisher(factory, run_id, *, status, demo=None, **values):
        entered.set()
        await finish.wait()
        async with demo.guard():
            saved.append(status)

    permit = slot_permit()
    try:
        response = s2r1b_student(run, run_id, [], permit=permit)
        response.finisher = finisher
        first = asyncio.create_task(response.demo_abort())
        await entered.wait()
        first.cancel()
        second = asyncio.create_task(response.demo_abort())
        await asyncio.sleep(0.05)
        # The cancelled caller keeps waiting; cleanup is still in progress.
        assert not first.done() and not second.done()
        assert permit in active_calls
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        await response.demo_abort()
        assert saved == ["interrupted"]
        assert permit not in active_calls and permit not in registered_calls
        assert run.token.task_control.done.is_set()
        assert lifecycle.lookup(scope, "run", run_id) is None
        assert lifecycle.visitors.lease_count(scope) == 0
    finally:
        permit.release()


async def test_abort_quiesces_an_active_producer_before_joining(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    started = asyncio.Event()

    async def execute_call(permit, request, **kwargs):
        started.set()
        await asyncio.Event().wait()
        yield CallEvent("completed", text="never")

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder([]))
    response = s2r1b_student(run, run_id, [])
    serving = asyncio.create_task(serve(response))
    await started.wait()
    # The body is mid-read; abort must not wait on the blocked producer.
    await asyncio.wait_for(response.demo_abort(), 10)
    assert all(task.done() for task in run.tasks)
    assert lifecycle.lookup(scope, "run", run_id) is None
    await asyncio.wait_for(serving, 10)
    assert lifecycle.visitors.lease_count(scope) == 0


async def test_s2r1b_demo_abort_after_a_served_run_is_a_noop(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    saved = []

    async def execute_call(permit, request, **kwargs):
        yield CallEvent("completed", text="Hi")

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    response = s2r1b_student(run, run_id, saved)
    await serve(response)
    await response.demo_abort()
    assert [status for status, _ in saved] == ["completed"]


async def test_s2r1b_normal_response_has_no_demo_abort_work():
    response = student_stream.StudentStreamingResponse(
        RunningDB,
        accepted(str(uuid4())),
        dict(permit=fake_permit(), request=None),
    )
    try:
        await response.demo_abort()
        assert response.abort_task is None
    finally:
        student_stream.active_runs.pop(response.run_id, None)
        await response.body_iterator.aclose()


async def test_s2r1b_student_producer_runs_on_its_child_context(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    parent = run.binding.ctx
    permit = s2r1b_permit(parent)
    original = S2r1bRequest(parent)
    seen = {}

    async def execute_call(call_permit, request, **kwargs):
        child = request.demo_context
        seen.update(
            same=call_permit.ctx is child,
            child=child,
            parent=child is parent,
            released=child.lease.released,
            lease=child.lease.lease_id != parent.lease.lease_id,
            factory=child.factory is parent.factory,
        )
        yield CallEvent("completed", text="Hi")

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder([]))
    await serve(s2r1b_student(run, run_id, [], permit=permit, request=original))
    assert seen.pop("child").lease.released
    assert seen == dict(
        same=True, parent=False, released=False, lease=True, factory=True
    )
    assert original.demo_context is parent


async def test_s2r1b_mentor_bot_runs_on_its_child_context(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    parent = run.binding.ctx
    permit = s2r1b_permit(parent)
    seen = {}

    async def execute_call(call_permit, request, **kwargs):
        child = request.demo_context
        seen.update(
            same=call_permit.ctx is child,
            parent=child is parent,
            released=child.lease.released,
            lease=child.lease.lease_id != parent.lease.lease_id,
        )
        yield CallEvent(
            "completed", structured=dict(feedback="c", reason_summary="r")
        )

    monkeypatch.setattr(tutor_bot, "execute_call", execute_call)
    monkeypatch.setattr(mentor_stream, "finish_mentor", recorder([]))
    await serve(
        s2r1b_mentor(
            run, run_id, [], permit=permit, request=S2r1bRequest(parent)
        )
    )
    assert seen == dict(same=True, parent=False, released=False, lease=True)
    assert lifecycle.visitors.lease_count(parent.scope) == 0


@pytest.mark.parametrize("mentor", [False, True])
async def test_s2r1b_release_waits_for_the_producer(
    lifecycle, monkeypatch, mentor
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    started, ended = asyncio.Event(), []

    async def execute_call(permit, request, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Work that outlives bounded cleanup, even a second cancel.
            until = time.monotonic() + 0.4
            while time.monotonic() < until:
                with suppress(asyncio.CancelledError):
                    await asyncio.sleep(0.02)
            ended.append(time.monotonic())
            raise
        yield CallEvent("completed", text="never")

    monkeypatch.setattr(student_stream, "CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(mentor_stream, "CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(tutor_bot, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder([]))
    monkeypatch.setattr(mentor_stream, "finish_mentor", recorder([]))
    response = (s2r1b_mentor if mentor else s2r1b_student)(run, run_id, [])
    serving = asyncio.create_task(serve(response))
    await started.wait()
    assert lifecycle.cancel(scope, "run", run_id) == 1
    await asyncio.sleep(0.25)
    # Bounded cleanup is over, the producer is not.
    assert ended == []
    assert not serving.done()
    assert lifecycle.lookup(scope, "run", run_id) is not None
    assert not run.token.task_control.done.is_set()
    await serving
    assert len(ended) == 1
    assert run.token.task_control.done.is_set()
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert lifecycle.visitors.lease_count(scope) == 0


@pytest.mark.parametrize(
    ("event", "recovery"),
    [
        (CallEvent("interrupted", error_code="interrupted"), "demo.stopped"),
        (
            CallEvent("error", error_code="configuration_unavailable"),
            "demo.unavailable",
        ),
        (CallEvent("error", error_code="empty_response"), "demo.output_failed"),
    ],
)
async def test_s2r1b_demo_recovery_follows_the_canonical_code(
    lifecycle, monkeypatch, event, recovery
):
    run, run_id = await demo_run(lifecycle)
    saved = []

    async def execute_call(permit, request, **kwargs):
        yield event

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    frames = await serve(s2r1b_student(run, run_id, saved))
    kind, data = frames[-1]
    alias = {
        "interrupted": "configuration_unavailable",
        "empty_response": "empty_output",
    }.get(event.error_code, event.error_code)
    assert kind == "run.failed"
    # The SSE alias and the stored code stay as they were.
    assert data["code"] == alias
    assert [v["error_code"] for _, v in saved] == [alias]
    assert data["recovery"] == recovery


async def test_s2r1b_student_revoked_before_acceptance_sends_only_terminal(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    saved = []

    async def execute_call(permit, request, **kwargs):
        raise AssertionError("revoked run must not start")
        yield

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", recorder(saved))
    await s2r1b_revoke(run)
    frames = await serve(s2r1b_student(run, run_id, saved))
    assert [kind for kind, _ in frames] == ["run.cancelled"]
    assert frames[0][1]["code"] == "DEMO_STALE_GENERATION"
    assert frames[0][1]["recovery"] == "demo.stale_generation"
    assert saved == []
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert lifecycle.visitors.lease_count(scope) == 0


async def test_s2r1b_mentor_revoked_before_acceptance_sends_only_terminal(
    lifecycle, monkeypatch
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    saved = []

    async def execute_call(permit, request, **kwargs):
        raise AssertionError("revoked run must not start")
        yield

    monkeypatch.setattr(tutor_bot, "execute_call", execute_call)
    monkeypatch.setattr(mentor_stream, "finish_mentor", recorder(saved))
    await s2r1b_revoke(run)
    frames = await serve(s2r1b_mentor(run, run_id, saved))
    assert [kind for kind, _ in frames] == ["run.cancelled"]
    assert frames[0][1]["code"] == "DEMO_STALE_GENERATION"
    assert saved == []
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert lifecycle.visitors.lease_count(scope) == 0


@pytest.mark.parametrize("mentor", [False, True])
async def test_s2r1b_revoked_cached_state_sends_no_cached_terminal(
    lifecycle, monkeypatch, mentor
):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    module = mentor_stream if mentor else student_stream
    monkeypatch.setattr(module, "snapshot", s2r1b_snapshot)
    db = s2r1b_done_db(run)
    frames = await serve(
        (s2r1b_mentor if mentor else s2r1b_student)(run, run_id, [], db=db)
    )
    assert [kind for kind, _ in frames] == ["run.accepted", "run.cancelled"]
    assert frames[-1][1]["code"] == "DEMO_STALE_GENERATION"
    assert "cached" not in repr(frames)
    assert lifecycle.lookup(scope, "run", run_id) is None


@pytest.mark.parametrize("provenance", [None, dict(response_source="prepared")])
async def test_s2r1b_student_terminals_carry_result_provenance(
    lifecycle, monkeypatch, provenance
):
    run, run_id = await demo_run(lifecycle)
    finish = s2r1b_with_provenance(recorder([]), provenance)
    events = iter(
        [CallEvent("completed", text="Hi"), CallEvent("error", error_code="x")]
    )

    async def execute_call(permit, request, **kwargs):
        yield next(events)

    monkeypatch.setattr(student_stream, "execute_call", execute_call)
    monkeypatch.setattr(student_stream, "finish_student", finish)
    for kind in ("output.completed", "run.failed"):
        if kind == "run.failed":
            run, run_id = await demo_run(lifecycle)
        frames = await serve(s2r1b_student(run, run_id, []))
        assert frames[-1][0] == kind
        if provenance is None:
            assert "provenance" not in frames[-1][1]
        else:
            assert frames[-1][1]["provenance"] == provenance


@pytest.mark.parametrize("provenance", [None, dict(response_source="prepared")])
async def test_s2r1b_mentor_terminal_carries_result_provenance(
    lifecycle, monkeypatch, provenance
):
    run, run_id = await demo_run(lifecycle)

    async def execute_call(permit, request, **kwargs):
        yield CallEvent(
            "completed", structured=dict(feedback="c", reason_summary="r")
        )

    monkeypatch.setattr(tutor_bot, "execute_call", execute_call)
    monkeypatch.setattr(
        mentor_stream,
        "finish_mentor",
        s2r1b_with_provenance(recorder([]), provenance),
    )
    frames = await serve(s2r1b_mentor(run, run_id, []))
    assert frames[-1][0] == "output.completed"
    assert frames[-1][1].get("provenance") == provenance
    assert ("provenance" in frames[-1][1]) is (provenance is not None)


async def test_s2r1b_release_holds_done_and_pop_for_tracked_tasks(lifecycle):
    run, run_id = await demo_run(lifecycle)
    scope = run.binding.ctx.scope
    gate = asyncio.Event()

    async def child_work():
        await gate.wait()
        raise RuntimeError("producer failed")

    work = asyncio.create_task(child_work())
    run.track(work)
    releasing = asyncio.create_task(run.release())
    await asyncio.sleep(0.05)
    assert not releasing.done()
    assert not run.token.task_control.done.is_set()
    assert lifecycle.lookup(scope, "run", run_id) is not None
    gate.set()
    # The producer's own failure never blocks the end of the run.
    await releasing
    assert run.token.task_control.done.is_set()
    assert lifecycle.lookup(scope, "run", run_id) is None
    assert lifecycle.visitors.lease_count(scope) == 0


S2R1C_VERSIONS = dict(
    contract_version="d0-03-v1-r3",
    content_version="d0-02-v1",
    fixture_version="demo-fixture-v1",
)
S2R1C_CTX = SimpleNamespace(generation=7, auth_epoch=3)


def s2r1c_expected(origin="visitor_generated"):
    return dict(
        mode="demo",
        response_source="prepared_example",
        external_ai_called=False,
        **S2R1C_VERSIONS,
        record_origin=origin,
    )


def s2r1c_error(code, status=400, headers=None):
    return HTTPException(status, detail=dict(code=code), headers=headers)


@pytest.mark.parametrize("code", sorted(DEMO_ERRORS))
def test_s2r1c_known_codes_get_the_section_11_envelope(code):
    status, retryable, recovery = DEMO_ERRORS[code]
    response = demo_error_response(s2r1c_error(code, 503), S2R1C_CTX)
    assert response.status_code == status
    assert json.loads(response.body) == dict(
        detail=dict(
            code=code,
            retryable=retryable,
            recovery=recovery,
            generation=7,
            auth_epoch=3,
        )
    )


def test_s2r1c_envelope_table_matches_section_11():
    assert DEMO_ERRORS == {
        "configuration_unavailable": (422, False, "demo.unavailable"),
        "call_limit_reached": (429, True, "demo.retry_later"),
        "context_limit": (422, False, "demo.supported_only"),
        "output_limit": (422, False, "demo.supported_only"),
        "session_ended": (409, False, "demo.stopped"),
    }


@pytest.mark.parametrize(
    "legacy,code,retryable,recovery",
    [
        ("student_busy", "DEMO_BUSY", True, "demo.wait_or_stop"),
        ("mentor_busy", "DEMO_BUSY", True, "demo.wait_or_stop"),
        (
            "unresolved_turn",
            "DEMO_REQUEST_CONFLICT",
            False,
            "demo.refresh_state",
        ),
    ],
)
def test_legacy_reservation_codes_become_their_section_11_rows(
    legacy, code, retryable, recovery
):
    # Literal §11 rows, independent of the mapping tables under test.
    response = demo_error_response(s2r1c_error(legacy, 409), S2R1C_CTX)
    assert response.status_code == 409
    assert json.loads(response.body) == dict(
        detail=dict(
            code=code,
            retryable=retryable,
            recovery=recovery,
            generation=7,
            auth_epoch=3,
        )
    )


def test_s2r1c_unknown_or_plain_errors_pass_through():
    for error in (
        s2r1c_error("mentor_disabled"),
        s2r1c_error("request_conflict", 409),
        HTTPException(404, detail="not found"),
    ):
        assert demo_error_response(error, S2R1C_CTX) is None
    # Normal mode has no context, so even a known code is left alone.
    assert demo_error_response(s2r1c_error("student_busy", 409), None) is None


def test_s2r1c_retry_after_is_preserved():
    error = s2r1c_error("call_limit_reached", 429, {"Retry-After": "1"})
    response = demo_error_response(error, S2R1C_CTX)
    assert response.headers["retry-after"] == "1"
    assert (
        "retry-after"
        not in demo_error_response(
            s2r1c_error("session_ended"), S2R1C_CTX
        ).headers
    )


async def s2r1c_raised(call):
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value


async def test_s2r1c_real_student_busy_maps_to_wait_or_stop(s2r_demo):
    demo = s2r_demo
    _, execution = await s2r_reserve(demo, demo.inputs["A"])
    execution["permit"].release()
    error = await s2r1c_raised(s2r_reserve(demo, demo.inputs["B"]))
    assert error.detail["code"] == "student_busy"
    response = demo_error_response(error, demo.binding.ctx)
    assert response.status_code == 409
    assert json.loads(response.body)["detail"] == dict(
        code="DEMO_BUSY",
        retryable=True,
        recovery="demo.wait_or_stop",
        generation=demo.ctx.generation,
        auth_epoch=demo.ctx.auth_epoch,
    )


async def test_s2r1c_real_unresolved_turn_maps_to_refresh_state(s2r_demo):
    demo = s2r_demo
    run_id = str(uuid4())
    _, execution = await s2r_reserve(demo, demo.inputs["A"], None, run_id)
    execution["permit"].release()
    await finish_student(
        demo.data.factory,
        run_id,
        status="failed",
        error_code="refused",
        demo=demo.binding,
    )
    error = await s2r1c_raised(s2r_reserve(demo, demo.inputs["B"]))
    response = demo_error_response(error, demo.binding.ctx)
    assert (response.status_code, error.detail["code"]) == (
        409,
        "unresolved_turn",
    )
    assert json.loads(response.body)["detail"]["code"] == (
        "DEMO_REQUEST_CONFLICT"
    )
    assert json.loads(response.body)["detail"]["recovery"] == (
        "demo.refresh_state"
    )


async def test_s2r1c_real_session_ended_maps_to_stopped(s2r_demo):
    demo = s2r_demo
    async with demo.data.factory() as db:
        await db.execute(
            update(LessonSession)
            .where(LessonSession.id == demo.data.session.id)
            .values(ended_at=datetime.now(timezone.utc))
        )
        await db.commit()
    error = await s2r1c_raised(s2r_reserve(demo, demo.inputs["A"]))
    response = demo_error_response(error, demo.binding.ctx)
    assert response.status_code == 409
    assert json.loads(response.body)["detail"]["recovery"] == "demo.stopped"


async def test_s2r1c_real_mentor_busy_maps_to_wait_or_stop(s2r_demo):
    demo = s2r_demo
    turn_id = await s2r_completed_turn(demo)
    _, execution = await s2r_mentor(demo, turn_id)
    execution["permit"].release()
    error = await s2r1c_raised(s2r_mentor(demo, turn_id))
    assert error.detail["code"] == "mentor_busy"
    response = demo_error_response(error, demo.binding.ctx)
    assert (response.status_code, json.loads(response.body)["detail"]) == (
        409,
        dict(
            code="DEMO_BUSY",
            retryable=True,
            recovery="demo.wait_or_stop",
            generation=demo.ctx.generation,
            auth_epoch=demo.ctx.auth_epoch,
        ),
    )


async def test_s2r1c_accepted_and_replay_carry_provenance(s2r_demo):
    demo = s2r_demo
    request_id = str(uuid4())
    accepted, execution = await s2r_reserve(demo, demo.inputs["A"], request_id)
    execution["permit"].release()
    assert accepted["provenance"] == s2r1c_expected()
    replay, again = await s2r_reserve(demo, demo.inputs["A"], request_id)
    assert again is None
    assert replay["provenance"] == s2r1c_expected()


async def test_s2r1c_finish_student_carries_provenance(s2r_demo):
    demo = s2r_demo
    run_id = str(uuid4())
    _, execution = await s2r_reserve(demo, demo.inputs["A"], None, run_id)
    execution["permit"].release()
    result = await finish_student(
        demo.data.factory,
        run_id,
        status="completed",
        content="허구 응답",
        demo=demo.binding,
    )
    assert result["provenance"] == s2r1c_expected()


async def test_s2r1c_mentor_reservation_carries_provenance(s2r_demo):
    demo = s2r_demo
    turn_id = await s2r_completed_turn(demo)
    accepted, execution = await s2r_mentor(demo, turn_id)
    execution["permit"].release()
    assert accepted["provenance"] == s2r1c_expected()


async def test_s2r1c_completed_turn_shortcut_carries_provenance(s2r_demo):
    demo = s2r_demo
    turn_id = await s2r_completed_turn(demo)
    request = s2r_request(demo.inputs["A"])
    request.turn_id = turn_id
    shortcut, execution = await reserve_student(
        demo.data.factory,
        demo.data.session.id,
        demo.data.owner,
        request,
        demo=demo.binding,
    )
    assert execution is None
    assert shortcut["status"] == "completed"
    assert shortcut["provenance"] == s2r1c_expected()


async def test_s2r1c_seed_session_is_seed_prepared(s2r_demo):
    demo = s2r_demo
    path = demo.seed.manifest_path
    manifest = json.loads(path.read_text())
    manifest["seed_refs"] = dict(completed_session=demo.data.session.id)
    path.write_text(json.dumps(manifest))
    accepted, execution = await s2r_reserve(demo, demo.inputs["A"])
    execution["permit"].release()
    assert accepted["provenance"] == s2r1c_expected("seed_prepared")


async def test_s2r1c_normal_snapshot_has_no_provenance(s2r_demo):
    demo = s2r_demo
    accepted, execution = await s2r_reserve(demo, demo.inputs["A"])
    execution["permit"].release()
    async with demo.data.factory() as db:
        from src.models import GenerationRun

        run = await db.get(GenerationRun, accepted["run_id"])
        plain = await snapshot(db, run)
        with_demo = await snapshot(db, run, demo=demo.binding)
    assert "provenance" not in plain
    assert {k: v for k, v in with_demo.items() if k != "provenance"} == plain

"""D2-04 PR3: demo analysis ctx, child lease, write guard, retry and provenance.

Admission runs PR2's real §14 validator in demo server mode; recheck and
the context_budget demo branch are stubbed at their module boundary unless a
test restores them. Everything else is the real #191 visitor DB, #192
lifecycle, guarded ledger and #196 DemoProvider with fictional content.
"""

import asyncio
import csv
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from s4_record_fixtures import stored_result
from sqlalchemy import select
from starlette.requests import Request
from test_demo_d1_01_visitors import ready_context
from test_demo_d1_01_visitors import visitors as visitor_registry
from test_demo_provider import BINDING_INPUTS, binding_template, seed_binding
from test_demo_provider import prepared as provider_prepared
from test_export_deleted_teacher import (  # noqa: F401  (fixtures)
    authenticate,
    export_api,
    export_history,
)
from test_s4_csv import NEW_COLUMNS, TEACHER_COLUMNS

from src.api.routes import admin_session_export, session_analysis
from src.demo.database import DemoError
from src.demo.lifecycle import Lifecycle, NormalContext
from src.demo.provider import Selection
from src.models import ApiUsageLog, GenerationRun, Message
from src.models.provider_connection import now
from src.services import (
    analysis_chunks,
    analysis_invocations,
    analysis_pipeline,
    analysis_plan,
    anthropic_generation,
    call_admission,
    call_execution,
    google_generation,
    invocation_ledger,
    openai_generation,
)
from src.services.analysis_invocations import AnalysisCaller
from src.services.analysis_results import load_analysis_response, save_analysis
from src.services.call_admission import (
    CallPermit,
    active_calls,
    registered_calls,
)
from src.services.lesson_snapshots import LessonSnapshot

__all__ = ["stored_result", "export_api", "export_history"]
prepared = provider_prepared
visitors = visitor_registry
TIMEOUTS = dict(
    student_total=5, mentor_total=5, analysis_total=5, model_list_total=5
)
PROVENANCE = dict(
    mode="demo",
    response_source="prepared_example",
    external_ai_called=False,
    contract_version="d0-03-v1-r3",
    content_version="d0-02-v1",
    fixture_version="demo-fixture-v1",
    record_origin="visitor_generated",
)
SNAPSHOT = LessonSnapshot.model_validate(
    dict(
        schema_version=1,
        scenario_context=dict(title="Fictional", subject="", target_grade=""),
        config=dict(
            problem={},
            student={},
            mentor={},
            runtime={},
            analysis=dict(
                rubric=[
                    dict(id="r1", name="one", criteria="Fictional one"),
                    dict(id="r3", name="three", criteria="Fictional three"),
                ],
                resolved_model_config=dict(
                    model_config_id=1,
                    provider_connection_id=1,
                    provider="demo",
                    model_id="demo-classroom-v1",
                    options={"max_output_tokens": 4096},
                ),
            ),
        ),
    )
)


@pytest.fixture
async def demo(prepared, visitors, monkeypatch):
    p = prepared
    _, ready, ctx = await ready_context(visitors, "teacher")
    await seed_binding(ctx, visitors)
    registry = Lifecycle(visitors)
    # Session 1 is not the seed's completed example: visitor_generated.
    p.manifest["seed_refs"]["completed_session"] = 99
    seen = SimpleNamespace(calls=[], permits=[], sdk=[], admin=None)

    def configure(case="normal", **changes):
        p.configure(
            dict(
                p.envelope,
                case_id=f"{case}/analysis/after_compare/A/common/none/single",
                role="analysis",
                content_state="after_compare",
                branch="A",
                input_kind="common",
                analysis_phase="single",
                trigger=None,
                text=None,
                structured=binding_template(),
            )
            | changes
        )
        adapter = p.provider
        adapter.visitors, adapter.inputs, adapter.session_id = (
            visitors,
            BINDING_INPUTS,
            1,
        )
        adapter.allow_failure_cases = case != "normal"
        original = adapter.generate_structured

        async def spy(request, secret, timeouts, *, deadline=None):
            seen.calls.append(
                (request, secret, visitors.lease_count(ctx.scope))
            )
            return await original(request, secret, timeouts, deadline=deadline)

        adapter.generate_structured = spy
        return adapter

    def forbidden(*args, **kwargs):
        seen.sdk.append(args)
        raise AssertionError("external SDK reached")

    for module in (openai_generation, anthropic_generation, google_generation):
        for name in ("generate_text", "stream_text", "generate_structured"):
            monkeypatch.setattr(module, name, forbidden)

    # Server mode is fixed at boot; tests swap only the config these read.
    server = SimpleNamespace(
        APP_MODE="demo", DEMO_MAX_ACTIVE_GLOBAL=8, DEMO_MAX_ACTIVE_VISITOR=2
    )
    for module in (call_admission, call_execution, analysis_invocations):
        monkeypatch.setattr(module, "config", server)

    async def admit_call(factory, *, ctx=None, registry=None, **values):
        # PR2's real validator: this call's DemoContext, registry and factory.
        call_admission.require_mode(factory, values["operation"], ctx, registry)
        await registry.assert_current(ctx)
        permit = CallPermit(
            factory,
            values["connection_id"],
            "demo",
            1,
            values["owner_id"],
            values["operation"],
            values["role"],
            values["admin"] if seen.admin is None else seen.admin,
            TIMEOUTS,
            None,
            asyncio.get_running_loop().time(),
            asyncio.current_task(),
            1,
            model_id="demo-classroom-v1",
            ctx=ctx,
            registry=registry,
        )
        active_calls.add(permit)
        registered_calls.add(permit)
        seen.permits.append(permit)
        return permit

    async def readmit_call(permit):
        active_calls.add(permit)
        return permit

    async def recheck_call(permit):
        return None

    async def resolve_frozen_model(db, selection, role, *, app_mode):
        return (
            SimpleNamespace(provider="demo", id=1, connection_version=1),
            SimpleNamespace(
                id=1, model_id="demo-classroom-v1", config_version=1
            ),
            {"max_output_tokens": 4096},
        )

    async def load_analysis_lesson(db, session_id, actor_id, **kwargs):
        return SNAPSHOT

    monkeypatch.setattr(analysis_invocations, "admit_call", admit_call)
    monkeypatch.setattr(call_execution, "readmit_call", readmit_call)
    monkeypatch.setattr(call_execution, "recheck_call", recheck_call)
    monkeypatch.setattr(
        analysis_invocations, "resolve_frozen_model", resolve_frozen_model
    )
    monkeypatch.setattr(
        analysis_pipeline, "load_analysis_lesson", load_analysis_lesson
    )
    # PR2's context_budget demo branch: a fixed local adapter has no SDK body.
    monkeypatch.setattr(
        analysis_plan,
        "provider_parameters",
        lambda request: dict(messages=request.messages),
    )
    async with ctx.factory() as db:
        messages = list(
            await db.scalars(
                select(Message)
                .where(Message.session_id == 1)
                .order_by(Message.created_at, Message.id)
            )
        )
    return SimpleNamespace(
        p=p,
        ctx=ctx,
        ready=ready,
        visitors=visitors,
        registry=registry,
        seen=seen,
        configure=configure,
        messages=messages,
    )


async def run_single(d, adapter, ctx=None):
    return await analysis_pipeline.run_llm_pipeline(
        1,
        d.messages,
        [m for m in d.messages if m.role == "teacher"],
        SNAPSHOT,
        d.ctx.factory,
        2,
        2,
        request_id="fictional-request",
        ctx=ctx or d.ctx,
        registry=d.registry,
        demo_adapter=adapter,
    )


async def ledger(ctx):
    async with ctx.factory() as db:
        return list(
            await db.scalars(select(ApiUsageLog).order_by(ApiUsageLog.id))
        )


async def stale_role(d):
    async with d.visitors.transition(d.ready.verified_visitor) as handle:
        handle.change_role(d.ctx, "admin", False, user_id=1)


async def test_demo_analysis_uses_child_lease_selection_and_local_adapter(
    demo,
):
    d = demo
    adapter = d.configure()
    _, _, payload, status, model, _, usage = await run_single(d, adapter)
    assert status == "ok" and model == "demo-classroom-v1" and usage == []
    assert [c["message_id"] for c in payload["message_classifications"]] == [
        1,
        3,
        5,
    ]
    assert payload["metadata"]["provenance"] == PROVENANCE
    [(request, secret, leases)] = d.seen.calls
    assert secret is None and d.seen.sdk == []
    assert request.demo_selection == Selection(
        role="analysis",
        content_state="after_compare",
        branch="A",
        input_kind="common",
        analysis_phase="single",
        manifest_sha256=d.p.selection.manifest_sha256,
    )
    # §6: the call ran on its own child lease, released in its finally.
    assert request.demo_context.lease is not d.ctx.lease
    assert request.demo_context.scope == d.ctx.scope and leases == 2
    assert request.demo_context.lease.released
    assert d.visitors.lease_count(d.ctx.scope) == 1
    [entry] = await ledger(d.ctx)
    assert (entry.provider, entry.status, entry.attempt_no) == (
        "demo",
        "completed",
        1,
    )
    # §19: unknown usage stays null, never 0.
    assert entry.input_tokens is None and entry.total_tokens is None
    assert entry.estimated_cost_usd is None and entry.usage_complete is False
    assert all(p_ not in registered_calls for p_ in d.seen.permits)


async def test_stale_ctx_refuses_the_call_before_ledger_or_adapter(demo):
    d = demo
    adapter = d.configure()
    await stale_role(d)
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        await run_single(d, adapter)
    assert d.seen.calls == [] and d.seen.permits == []
    assert await ledger(d.ctx) == []
    assert d.visitors.lease_count(d.ctx.scope) == 1


async def test_teacher_analysis_retries_transient_exactly_once(
    demo, monkeypatch
):
    d = demo
    adapter = d.configure(
        "transient", terminal="error", error_code="transient", structured=None
    )
    original = AnalysisCaller.demo_selection
    monkeypatch.setattr(
        AnalysisCaller,
        "demo_selection",
        lambda self, operation: replace(
            original(self, operation), fixture_case="transient"
        ),
    )
    _, _, payload, status, _, _, _ = await run_single(d, adapter)
    assert status == "failed"
    assert payload["metadata"]["error_code"] == "transient"
    rows = await ledger(d.ctx)
    assert [(r.attempt_no, r.status, r.error_code) for r in rows] == [
        (1, "failed", "transient"),
        (2, "failed", "transient"),
    ]
    assert rows[1].retry_wait_ms == 1000 and len(d.seen.calls) == 2


async def test_admin_analysis_permit_is_not_retried(demo, monkeypatch):
    d = demo
    adapter = d.configure(
        "transient", terminal="error", error_code="transient", structured=None
    )
    original = AnalysisCaller.demo_selection
    monkeypatch.setattr(
        AnalysisCaller,
        "demo_selection",
        lambda self, operation: replace(
            original(self, operation), fixture_case="transient"
        ),
    )
    d.seen.admin = True
    await run_single(d, adapter)
    assert [r.attempt_no for r in await ledger(d.ctx)] == [1]


async def test_ledger_rows_are_written_only_under_a_current_write_guard(
    demo,
):
    d = demo
    values = dict(
        request_id="fictional-ledger",
        owner_id=2,
        provider="demo",
        model="demo-classroom-v1",
        role="analysis",
        operation="analysis_unified",
        credential_revision=1,
        session_id=1,
    )
    # A visitor DB refuses raw writes outside the guard (#191).
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        await invocation_ledger.start_attempt(d.ctx.factory, **values)
    entry_id = await invocation_ledger.start_attempt(
        d.ctx.factory, **values, ctx=d.ctx, registry=d.registry
    )
    await stale_role(d)
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        await invocation_ledger.start_attempt(
            d.ctx.factory, **values, ctx=d.ctx, registry=d.registry
        )
    with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
        await invocation_ledger.finish_attempt(
            d.ctx.factory,
            entry_id,
            status="completed",
            ctx=d.ctx,
            registry=d.registry,
        )
    [entry] = await ledger(d.ctx)
    assert entry.status == "running" and entry.finished_at is None


@pytest.mark.parametrize(
    "operation,phase",
    [
        ("analysis_unified", "single"),
        ("analysis_chunk", "chunk"),
        ("analysis_merge", "merge"),
    ],
)
async def test_demo_selection_phase_follows_the_operation(
    demo, operation, phase
):
    d = demo
    caller = AnalysisCaller(
        d.ctx.factory,
        selection=SNAPSHOT.config.analysis.resolved_model_config,
        session_id=1,
        owner_id=2,
        demo_adapter=d.configure(),
        messages=d.messages,
    )
    selection = caller.demo_selection(operation)
    assert selection.analysis_phase == phase
    assert (selection.content_state, selection.branch) == ("after_compare", "A")


async def test_chunk_pipeline_forwards_ctx_and_adapter(monkeypatch):
    captured = {}

    class StopError(Exception):
        pass

    def caller(factory, **values):
        captured.update(values)
        raise StopError

    monkeypatch.setattr(analysis_chunks, "AnalysisCaller", caller)
    ctx, registry, adapter = object(), object(), object()
    with pytest.raises(StopError):
        await analysis_chunks.run_chunk_pipeline(
            1,
            ["message"],
            [],
            SNAPSHOT,
            None,
            plan=dict(chunks=[]),
            ctx=ctx,
            registry=registry,
            demo_adapter=adapter,
        )
    assert captured["ctx"] is ctx and captured["registry"] is registry
    assert captured["demo_adapter"] is adapter
    assert captured["messages"] == ["message"]


async def test_normal_caller_sends_no_demo_fields(monkeypatch):
    seen = {}

    async def execute_call(permit, request, **kwargs):
        seen.update(request=request, kwargs=kwargs)
        yield SimpleNamespace(
            type="completed", usage=None, structured={"ok": True}
        )

    async def admit_call(factory, **values):
        return SimpleNamespace(timeouts={"analysis_total": 5})

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def resolve_frozen_model(db, selection, role, *, app_mode):
        assert app_mode == "normal"
        return (
            SimpleNamespace(provider="openai", id=1, connection_version=1),
            SimpleNamespace(id=1, config_version=1),
            {},
        )

    async def load_analysis_lesson(db, session_id, actor_id):
        return None

    monkeypatch.setattr(analysis_invocations, "execute_call", execute_call)
    monkeypatch.setattr(analysis_invocations, "admit_call", admit_call)
    monkeypatch.setattr(
        analysis_invocations, "resolve_frozen_model", resolve_frozen_model
    )
    monkeypatch.setattr(
        analysis_pipeline, "load_analysis_lesson", load_analysis_lesson
    )
    caller = AnalysisCaller(
        Database,
        selection=SimpleNamespace(model_id="gpt-5-mini"),
        session_id=1,
        owner_id=2,
    )
    assert isinstance(caller.ctx, NormalContext) and caller.provenance() is None
    assert await caller.structured("prompt", object, "analysis_unified") == (
        {"ok": True},
        None,
    )
    assert seen["request"].demo_context is None
    assert seen["request"].demo_selection is None
    assert "app_mode" not in seen["kwargs"]
    assert "demo_adapter" not in seen["kwargs"]


ANALYSIS = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@ANALYSIS
async def test_saved_provenance_reaches_run_and_analysis_json(
    data, monkeypatch
):
    from analysis_fixtures import install_analysis_snapshot

    await install_analysis_snapshot(data, monkeypatch)
    payloads = {}
    for name, metadata in (
        ("normal", {}),
        ("demo", dict(provenance=PROVENANCE)),
    ):
        run = GenerationRun(
            id=f"{name}-run",
            owner_id=data.owner.id,
            session_id=data.session.id,
            turn_id=f"{name}-run",
            operation="analysis",
            request_id=f"{name}-request",
            input_hash="hash",
            config_hash="hash",
            provider="openai",
            model="gpt-5-mini",
            status="running",
            plan_json="{}",
            started_at=now(),
        )
        data.db.add(run)
        await data.db.commit()
        payload = dict(
            schema_version=2,
            message_classifications=[],
            misconception_findings=[],
            brief_feedback=["Fictional"],
            strengths=[],
            improvements=[],
            dialogue_coaching=[],
            metadata=dict(outcome="ok", **metadata),
        )
        await save_analysis(
            data.session.id,
            ({}, [], payload, "ok", "gpt-5-mini", "hash", []),
            data.db,
            run_id=run.id,
        )
        await data.db.refresh(run)
        payloads[name] = (
            json.loads(run.outcome_json),
            await load_analysis_response(data.session.id, data.db),
        )
    normal_outcome, normal_response = payloads["normal"]
    demo_outcome, demo_response = payloads["demo"]
    assert "provenance" not in normal_outcome
    assert "provenance" not in normal_response
    assert demo_outcome.pop("provenance") == PROVENANCE
    assert demo_outcome.keys() == normal_outcome.keys()
    assert demo_response["provenance"] == PROVENANCE


RUNTIME_LIFECYCLE = Lifecycle()


def route_request(ctx):
    app = SimpleNamespace(
        state=SimpleNamespace(
            demo_request_db=SimpleNamespace(lifecycle=RUNTIME_LIFECYCLE)
        )
    )
    return Request(
        dict(
            type="http",
            method="POST",
            path="/",
            headers=[],
            query_string=b"",
            app=app,
            state={} if ctx is None else dict(demo_context=ctx),
        )
    )


async def test_normal_teacher_routes_keep_the_helper_defaults(monkeypatch):
    ctx = None
    calls = []

    async def capture(*args, **kwargs):
        calls.append({k: kwargs[k] for k in ("ctx", "registry") if k in kwargs})
        return {}

    async def load_session(*args, **kwargs):
        return SimpleNamespace(ended_at=None)

    async def mark_session_ended(*args, **kwargs):
        return now(), None

    monkeypatch.setattr(session_analysis, "request_analysis", capture)
    monkeypatch.setattr(session_analysis, "cancel_analysis", capture)
    monkeypatch.setattr(session_analysis, "load_session", load_session)
    monkeypatch.setattr(
        session_analysis, "mark_session_ended", mark_session_ended
    )
    user, body = SimpleNamespace(id=2), SimpleNamespace(
        request_id="r", plan_hash=None
    )
    await session_analysis.end_session(route_request(ctx), 1, body, user, None)
    await session_analysis.analyze_session_endpoint(
        route_request(ctx), body, 1, user, None
    )
    await session_analysis.cancel_analysis_run(
        route_request(ctx), 1, "run", user, None
    )
    # Normal keeps #192's defaults: no ctx or registry keyword at all.
    assert calls == [{}] * 3


@pytest.mark.parametrize("mode", ["single", "chunked"])
@pytest.mark.parametrize("scope", ["normal", "demo"])
async def test_execute_analysis_forwards_demo_ctx_registry_and_adapter(
    monkeypatch, mode, scope
):
    from contextlib import asynccontextmanager

    from src.services import analysis_runs

    received = []

    async def pipeline(*args, **kwargs):
        received.append(kwargs)
        return "result"

    async def save_analysis(*args, **kwargs):
        return None

    class Registry:
        async def assert_current(self, ctx):
            return None

        @asynccontextmanager
        async def write_guard(self, ctx):
            yield None

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(analysis_pipeline, "run_llm_pipeline", pipeline)
    monkeypatch.setattr(analysis_runs, "chunk_executor", pipeline)
    monkeypatch.setattr(analysis_runs, "save_analysis", save_analysis)
    registry, adapter = Registry(), object()
    ctx = (
        NormalContext(Database)
        if scope == "normal"
        else SimpleNamespace(
            app_mode="demo",
            visitor_id="00000000-0000-4000-8000-000000000001",
            generation=1,
            auth_epoch=1,
            lease=object(),
            factory=Database,
        )
    )
    execution = dict(
        run_id="run",
        session_id=1,
        all_messages=[],
        teacher_messages=[],
        snapshot=None,
        owner_id=2,
        actor_id=2,
        request_id="request",
        regenerate=False,
        started_at=now(),
        plan=dict(mode=mode),
        demo_adapter=adapter,
    )
    await analysis_runs.execute_analysis(
        Database, execution, ctx=ctx, registry=registry
    )
    [kwargs] = received
    extra = {
        k: kwargs[k] for k in ("ctx", "registry", "demo_adapter") if k in kwargs
    }
    assert extra == (
        {}
        if scope == "normal"
        else dict(ctx=ctx, registry=registry, demo_adapter=adapter)
    )
    assert ("plan" in kwargs) == (mode == "chunked")


CSV_CASE = pytest.mark.parametrize(
    ("data", "stored_result"), [("baseline", "ok")], indirect=True
)


@CSV_CASE
@pytest.mark.parametrize("mode", ["normal", "demo"])
async def test_teacher_export_passes_the_seed_session_in_demo_only(
    data, stored_result, export_api, monkeypatch, mode
):
    if mode == "demo":
        demo = SimpleNamespace(APP_MODE="demo")
        monkeypatch.setattr(admin_session_export, "config", demo)
        monkeypatch.setattr(session_analysis, "config", demo)
        from src.main import app

        monkeypatch.setattr(
            app.state,
            "demo_request_db",
            SimpleNamespace(seed_refs={"completed_session": data.session.id}),
            raising=False,
        )
    await authenticate(export_api, data.owner)
    response = await export_api.get(f"/sessions/{data.session.id}/export.csv")
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.text.removeprefix("﻿")))
    rows = list(reader)
    disposition = response.headers["content-disposition"]
    if mode == "normal":
        assert reader.fieldnames == TEACHER_COLUMNS + NEW_COLUMNS
        assert disposition == (
            f"attachment; filename=session_{data.session.id}_analysis.csv"
        )
    else:
        assert reader.fieldnames[-5:] == [
            "demo_response_source",
            "demo_external_ai_called",
            "demo_content_version",
            "demo_fixture_version",
            "demo_record_origin",
        ]
        assert {row["demo_record_origin"] for row in rows} == {"seed_prepared"}
        assert disposition == (
            "attachment; filename="
            f"demo_d0-02-v1_teacher_session-{data.session.id}.csv"
        )
    assert rows


async def test_ledger_default_is_normal_context(monkeypatch):
    entered = []

    class Registry:
        def write_guard(self, ctx):
            entered.append(ctx)
            return NullGuard()

    class NullGuard:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):
            return None

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            return SimpleNamespace(rowcount=1)

        async def commit(self):
            return None

    assert await invocation_ledger.finish_attempt(
        Database, 1, status="completed", registry=Registry()
    )
    assert isinstance(entered[0], NormalContext)
    assert entered[0].factory is Database


async def request_session(d):
    """The #194 demo request session, with its own DML guard listeners."""
    from src.api.dependencies import _RequestDBState, _RequestSession

    state = _RequestDBState(
        SimpleNamespace(visitors=d.visitors), d.ctx.lease, d.ctx.factory()
    )
    state.ctx = d.ctx
    await state.watch_transactions()
    return state, _RequestSession(state)


def demo_route_request(d):
    app = SimpleNamespace(
        state=SimpleNamespace(
            demo_request_db=SimpleNamespace(lifecycle=d.registry)
        )
    )
    return Request(
        dict(
            type="http",
            method="POST",
            path="/",
            headers=[],
            query_string=b"",
            app=app,
            state=dict(demo_context=d.ctx),
        )
    )


def fictional_snapshot(monkeypatch):
    """Snapshot publication is not under test; the fictional one stands in."""
    from src.services import (
        analysis_results,
        analysis_runs,
        lesson_snapshots,
        session_history,
    )

    for module in (
        analysis_results,
        analysis_runs,
        lesson_snapshots,
        session_history,
    ):
        monkeypatch.setattr(
            module, "read_lesson_snapshot", lambda *a, **k: SNAPSHOT
        )


async def open_native_session(d, session_id=2):
    from sqlalchemy import text

    from src.services.lesson_snapshots import canonical_hash

    envelope = SNAPSHOT.model_dump()
    async with d.ctx.factory() as db, d.visitors.write_guard(d.ctx):
        await db.execute(
            text(
                "UPDATE session SET ended_at=NULL, snapshot_origin='native', "
                "config_snapshot_json=:j, config_hash=:h WHERE id=:id"
            ),
            dict(
                j=json.dumps(envelope),
                h=canonical_hash(envelope),
                id=session_id,
            ),
        )
        await db.commit()


async def test_demo_end_and_cancel_run_the_real_guarded_helpers(
    demo, monkeypatch
):
    from sqlalchemy import text

    from src.api.routes.session_helpers import mark_session_ended
    from src.models import Session
    from src.services import analysis_runs

    d = demo
    await open_native_session(d)
    state, db = await request_session(d)
    reserved = []

    async def load_session(session_id, user, db_, **kwargs):
        return await db_.get(Session, session_id)

    async def reserve_analysis(factory, session_id, *args, ctx, registry, **kw):
        reserved.append((factory, ctx, registry))
        return {"latest_run": None}, None

    monkeypatch.setattr(session_analysis, "load_session", load_session)
    monkeypatch.setattr(analysis_runs, "reserve_analysis", reserve_analysis)
    fictional_snapshot(monkeypatch)
    user = SimpleNamespace(id=2)
    body = SimpleNamespace(request_id="fictional-end", plan_hash=None)
    try:
        # The request session guards its own DML; a helper holding the same
        # guard over it would re-enter the visitor transition lock.
        session = await db.get(Session, 2)
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await mark_session_ended(
                session, db, force=True, ctx=d.ctx, registry=d.registry
            )
        await db.rollback()
        await session_analysis.end_session(
            demo_route_request(d), 2, body, user, db
        )
        async with d.ctx.factory() as check:
            ended = await check.scalar(
                text("SELECT ended_at FROM session WHERE id=2")
            )
        assert ended is not None
        [(factory, ctx, registry)] = reserved
        assert factory is d.ctx.factory and registry is d.registry
        assert ctx.scope == d.ctx.scope and ctx.lease is not d.ctx.lease
        async with d.ctx.factory() as raw, d.visitors.write_guard(d.ctx):
            await raw.execute(
                text(
                    "INSERT INTO generation_run(id,owner_id,session_id,turn_id,"
                    "operation,request_id,input_hash,config_hash,provider,"
                    "model,status,started_at) VALUES('analysis-run',2,2,"
                    "'analysis-run','analysis','fictional-cancel','hash',"
                    "'hash','demo','demo-classroom-v1','running','2026-10-19')"
                )
            )
            await raw.commit()
        await session_analysis.cancel_analysis_run(
            demo_route_request(d), 2, "analysis-run", user, db
        )
        async with d.ctx.factory() as check:
            status = await check.scalar(
                text(
                    "SELECT status FROM generation_run WHERE id='analysis-run'"
                )
            )
        assert status == "cancelled"
    finally:
        await state.session.close()
    assert d.visitors.lease_count(d.ctx.scope) == 1


async def test_demo_analysis_end_to_end_through_admission_and_ledger(
    demo, monkeypatch
):
    """reserve → real admission → execute_call → DemoProvider → guarded
    ledger → guarded save with provenance; zero external SDK calls."""
    from src.models import AppSetting, ModelConfig, ProviderConnection
    from src.services import analysis_runs
    from src.services.model_verification import (
        DEMO_SEED_TIME,
        ROLE_CONTRACT_VERSIONS,
    )

    d = demo
    async with d.ctx.factory() as db, d.visitors.write_guard(d.ctx):
        # Migrations may install keyless placeholders; find or add the demo pair.
        connection = await db.scalar(
            select(ProviderConnection).where(
                ProviderConnection.provider == "demo"
            )
        )
        if connection is None:
            connection = ProviderConnection(
                provider="demo",
                enabled=True,
                credential_revision=0,
                connection_version=1,
            )
            db.add(connection)
            await db.flush()
        model = await db.scalar(
            select(ModelConfig).where(
                ModelConfig.provider_connection_id == connection.id,
                ModelConfig.model_id == "demo-classroom-v1",
            )
        )
        if model is None:
            model = ModelConfig(
                provider_connection_id=connection.id,
                model_id="demo-classroom-v1",
                display_name="Demo",
                enabled=True,
                capability_definition_version="demo-cap-v1",
                default_options_json={"max_output_tokens": 4096},
                verification_state={
                    role: dict(
                        status="succeeded",
                        verified_at=DEMO_SEED_TIME.isoformat(),
                        credential_revision=0,
                        connection_version=1,
                        capability_definition_version="demo-cap-v1",
                        role_contract_version=version,
                    )
                    for role, version in ROLE_CONTRACT_VERSIONS.items()
                },
            )
            db.add(model)
            await db.flush()
        frozen = SNAPSHOT.model_dump()
        frozen["config"]["analysis"]["resolved_model_config"].update(
            model_config_id=model.id, provider_connection_id=connection.id
        )
        snapshot = LessonSnapshot.model_validate(frozen)
        setting = await db.get(AppSetting, 1)
        if setting is None:
            db.add(
                AppSetting(
                    id=1,
                    limits_json=dict(
                        total=2,
                        openai=2,
                        anthropic=2,
                        google=2,
                        admin=1,
                        demo=2,
                    ),
                    timeouts_json=dict(
                        connect=2,
                        student_first_output=5,
                        student_total=15,
                        mentor_first_output=5,
                        mentor_total=15,
                        analysis_total=30,
                        model_list_total=5,
                    ),
                )
            )
        await db.commit()
    from src.services.lesson_connections import resolve_frozen_model

    async def lesson(*args, **kwargs):
        return snapshot

    fictional_snapshot(monkeypatch)
    from src.services import analysis_results

    for module in (analysis_runs, analysis_results):
        monkeypatch.setattr(module, "load_analysis_lesson", lesson)
    monkeypatch.setattr(analysis_runs, "config", analysis_invocations.config)
    # Real frozen-model resolution, admission, recheck and readmission.
    monkeypatch.setattr(
        analysis_invocations, "resolve_frozen_model", resolve_frozen_model
    )
    monkeypatch.setattr(
        analysis_invocations, "admit_call", call_admission.admit_call
    )
    monkeypatch.setattr(
        call_execution, "recheck_call", call_admission.recheck_call
    )
    monkeypatch.setattr(
        call_execution, "readmit_call", call_admission.readmit_call
    )
    adapter = d.configure()
    await open_native_session(d, session_id=1)
    async with d.ctx.factory() as db, d.visitors.write_guard(d.ctx):
        from sqlalchemy import text

        await db.execute(
            text("UPDATE session SET ended_at='2026-10-19' WHERE id=1")
        )
        await db.commit()
    accepted, execution = await analysis_runs.reserve_analysis(
        d.ctx.factory,
        1,
        2,
        "fictional-e2e",
        ctx=d.ctx,
        registry=d.registry,
    )
    assert execution is not None
    execution["demo_adapter"] = adapter
    await analysis_runs.execute_analysis(
        d.ctx.factory, execution, ctx=d.ctx, registry=d.registry
    )
    async with d.ctx.factory() as db:
        run = await db.get(GenerationRun, execution["run_id"])
        assert run.status == "ok", run.outcome_json
        assert json.loads(run.outcome_json)["provenance"] == PROVENANCE
        response = await load_analysis_response(1, db)
    assert response["provenance"] == PROVENANCE
    [(request, secret, leases)] = d.seen.calls
    assert secret is None and d.seen.sdk == [] and leases == 2
    [entry] = await ledger(d.ctx)
    assert (entry.provider, entry.status, entry.run_id) == (
        "demo",
        "completed",
        execution["run_id"],
    )
    assert entry.usage_complete is False and entry.input_tokens is None
    assert not any(p.ctx is not None for p in active_calls)
    assert d.visitors.lease_count(d.ctx.scope) == 1

"""Prepared-provider boundaries, with fictional content confined to this file."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.demo.provider import (
    DemoProvider,
    Selection,
    fixture_lookup_key,
    select_content,
)
from src.demo.visitors import DemoContext
from src.services.invocation_types import TextRequest

DIGEST = "84c3988b5ab0c5ad9aa47e1c7c66edb755909e8b65a820eac9456bcf56dfaadb"
INPUTS = dict(A="첫 질문 A", B="첫 질문 B", C="첫 질문 C", common="공통 질문")


@dataclass(frozen=True)
class DemoTextRequest(TextRequest):
    demo_context: object = field(default=None, kw_only=True)
    demo_selection: object = field(default=None, kw_only=True)


class CheckedVisitors:
    def __init__(self):
        self.calls = 0
        self.stale = False

    async def check(self, context):
        self.calls += 1
        assert isinstance(context, DemoContext)
        if self.stale:
            from src.demo.database import DemoError

            raise DemoError("STALE_GENERATION")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


@pytest.fixture
async def prepared(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "visitor.db"}'
    )
    async with engine.begin() as db:
        for statement in (
            'CREATE TABLE "user" (id INTEGER PRIMARY KEY, role TEXT)',
            "CREATE TABLE session (id INTEGER PRIMARY KEY, teacher_id INTEGER, ended_at TEXT, deleted_at TEXT)",
            "CREATE TABLE message (id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT, turn_id TEXT, turn_index INTEGER, generation_run_id TEXT, created_at TEXT)",
            "CREATE TABLE generation_run (id TEXT PRIMARY KEY, session_id INTEGER, turn_id TEXT, operation TEXT, status TEXT, result_kind TEXT, owner_id INTEGER, finished_at TEXT)",
            'INSERT INTO "user" VALUES (1, "teacher")',
            "INSERT INTO session VALUES (2, 1, NULL, NULL)",
        ):
            await db.execute(text(statement))
    factory = async_sessionmaker(engine)
    context = DemoContext(
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
        factory=factory,
        lease=SimpleNamespace(released=False),
        verified_visitor=object(),
    )
    key = "normal/student/initial/A/A/none/none"
    envelope = dict(
        fixture_version="demo-fixture-v1",
        case_id=key,
        role="student",
        content_state="initial",
        branch="A",
        input_kind="A",
        trigger=None,
        analysis_phase="none",
        terminal="completed",
        error_code=None,
        text="허구 응답 🌟" * 20,
        structured=None,
        partial_text=None,
    )
    root = tmp_path / "fixtures"
    root.mkdir()
    manifest = dict(
        manifest_version=1,
        contract_version="d0-03-v1-r3",
        content_version="d0-02-v1",
        fixture_version="demo-fixture-v1",
        db_schema_version="future.sql",
        migration_set_sha256="a" * 64,
        scenario_config_schema_version=1,
        analysis_schema_version=2,
        provider="demo",
        model_id="demo-classroom-v1",
        capability_definition_version="demo-cap-v1",
        role_contract_versions=dict(
            student="s1-v1", mentor="s3-v1", analysis="s4-v2"
        ),
        scenario_ref="unit-fraction-v1",
        supported_config_sha256=DIGEST,
        seed_version="demo-seed-v1",
        seed_timestamp="2026-10-18T00:00:00Z",
        seed_refs=dict(
            admin=3,
            teacher=1,
            group=1,
            scenario=1,
            completed_session=1,
            model=1,
            connection=1,
        ),
        files=[],
        lookup_version=1,
        responses={},
        seed_provenance=dict(
            source="synthetic",
            generator="src/demo/seed.py",
            content_version="d0-02-v1",
        ),
    )
    visitors = CheckedVisitors()
    state = SimpleNamespace(
        root=root,
        manifest=manifest,
        envelope=envelope,
        context=context,
        visitors=visitors,
        factory=factory,
        hash_calls=[],
    )

    def configure(envelope=envelope):
        raw = encode(envelope)
        (root / "response.json").write_bytes(raw)
        manifest["files"] = [
            dict(
                path="response.json",
                sha256=hashlib.sha256(raw).hexdigest(),
                kind=(
                    "student"
                    if envelope["role"] == "student"
                    else envelope["role"]
                ),
            )
        ]
        manifest["responses"] = {envelope["case_id"]: "response.json"}
        (root / "manifest.json").write_bytes(encode(manifest))
        selection = Selection(
            manifest_sha256=hashlib.sha256(encode(manifest)).hexdigest(),
            **{
                k: envelope[k]
                for k in (
                    "role",
                    "content_state",
                    "branch",
                    "input_kind",
                    "trigger",
                    "analysis_phase",
                )
            },
            fixture_case=envelope["case_id"].split("/")[0],
        )
        state.selection = selection
        state.request = DemoTextRequest(
            "demo",
            "demo-classroom-v1",
            "student",
            "원래 지시",
            [dict(role="user", content=INPUTS["A"])],
            {"max_output_tokens": 4096},
            str(uuid4()),
            demo_context=context,
            demo_selection=selection,
        )

        def config_hash(version, config):
            state.hash_calls.append((version, config))
            return DIGEST

        state.provider = DemoProvider(
            root / "manifest.json",
            visitors=visitors,
            mode="demo",
            session_id=2,
            config_schema_version=1,
            config={},
            inputs=INPUTS,
            supported_config_sha256=config_hash,
            db_schema_version="future.sql",
            migration_set_sha256="a" * 64,
        )

    state.configure = configure
    configure()
    yield state
    await engine.dispose()


async def test_text_stream_share_body_and_unknown_usage(prepared):
    p = prepared
    text = await p.provider.generate_text(p.request, None, {"student_total": 2})
    events = [
        e
        async for e in p.provider.stream_text(
            p.request, None, {"student_total": 2}
        )
    ]
    assert text.type == "completed"
    assert (
        "".join(e.text for e in events if e.type == "text_delta")
        == text.text
        == events[-1].text
    )
    assert all(len(e.text) <= 32 for e in events if e.type == "text_delta")
    assert [e.type for e in events].count("completed") == 1
    assert text.usage["usage_complete"] is False
    assert all(
        value is None
        for key, value in text.usage.items()
        if key != "usage_complete"
    )
    assert p.hash_calls and p.visitors.calls >= 4
    assert (
        fixture_lookup_key(p.selection)
        == "normal/student/initial/A/A/none/none"
    )


@pytest.mark.parametrize("branch", ["A", "B", "C"])
def test_completed_history_selects_only_exact_inputs(branch):
    def turn(content):
        return dict(teacher=dict(content=content))

    assert select_content([], "  " + INPUTS[branch] + "\n", INPUTS) == (
        "initial",
        None,
        branch,
    )
    history = [turn("미지원"), turn(INPUTS[branch]), turn("미지원")]
    assert select_content(history, INPUTS["common"], INPUTS) == (
        "after_first",
        branch,
        "common",
    )
    assert (
        select_content(history, INPUTS["common"].replace(" ", "  "), INPUTS)[2]
        == "unsupported"
    )
    assert (
        select_content(history, "English paraphrase", INPUTS)[2]
        == "unsupported"
    )
    history.append(turn(INPUTS["common"]))
    assert select_content(history, INPUTS["A"], INPUTS) == (
        "after_compare",
        branch,
        "unsupported",
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(provider="openai"),
        dict(model_id="other"),
        dict(role="mentor"),
        dict(service_tier="default"),
        dict(demo_context=None),
        dict(demo_selection=None),
        *[
            dict(validated_options=options)
            for options in (
                {},
                {"max_output_tokens": True},
                {"max_output_tokens": 4095},
                {"max_output_tokens": 8193},
                {"max_output_tokens": 4096.0},
                {"max_output_tokens": 4096, "temperature": 0},
            )
        ],
    ],
)
async def test_boundary_rejects_without_external_attempts(
    prepared, monkeypatch, changes
):
    import socket

    import anthropic
    import httpx
    import httpx2
    import openai
    from google import genai

    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("External attempt")

    for owner, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (httpx.AsyncClient, "send"),
        (httpx2.AsyncClient, "send"),
        (openai, "AsyncOpenAI"),
        (anthropic, "AsyncAnthropic"),
        (genai, "Client"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    event = await prepared.provider.generate_text(
        replace(prepared.request, **changes), None, {"student_total": 2}
    )
    assert event.type == "error" and event.error_code == "INVALID_REQUEST"
    assert attempts == []


@pytest.mark.parametrize(
    "mode,secret", [("normal", None), ("demo", ""), ("demo", "key")]
)
async def test_normal_and_secrets_are_rejected(prepared, mode, secret):
    prepared.provider.mode = mode
    event = await prepared.provider.generate_text(
        prepared.request, secret, {"student_total": 2}
    )
    assert event.error_code == "INVALID_REQUEST"


@pytest.mark.parametrize("tokens", [4096, 8192])
async def test_option_boundaries_and_request_id_do_not_change_text(
    prepared, tokens
):
    first = await prepared.provider.generate_text(
        prepared.request, None, {"student_total": 2}
    )
    second = await prepared.provider.generate_text(
        replace(
            prepared.request,
            request_id=str(uuid4()),
            validated_options={"max_output_tokens": tokens},
        ),
        None,
        {"student_total": 2},
    )
    assert second.type == "completed" and first.text == second.text


@pytest.mark.parametrize(
    "case,terminal,code",
    [
        ("interrupted_after_delta", "interrupted", "interrupted"),
        ("refused", "refused", "refused"),
        *[
            (code, "error", code)
            for code in (
                "invalid_output",
                "invalid_json",
                "invalid_reference",
                "empty_response",
                "timeout_connect",
                "timeout_first_output",
                "timeout_total",
                "transient",
            )
        ],
        ("analysis_failed", "error", "invalid_output"),
    ],
)
async def test_failure_cases_are_deterministic_and_never_substituted(
    prepared, case, terminal, code
):
    p = prepared
    envelope = dict(
        p.envelope,
        case_id=f"{case}/student/initial/A/A/none/none",
        terminal=terminal,
        error_code=code,
    )
    if case == "interrupted_after_delta":
        envelope["partial_text"] = envelope["text"][:40]
    p.configure(envelope)
    p.provider.allow_failure_cases = True
    for _ in range(2):
        events = [
            e
            async for e in p.provider.stream_text(
                p.request, None, {"student_total": 2}
            )
        ]
        assert events[-1].type == terminal and events[-1].error_code == code
        assert len([e for e in events if e.type != "text_delta"]) == 1
        assert all(e.type == "text_delta" for e in events[:-1])
        assert events[-1].text == ""
        assert "".join(e.text for e in events[:-1]) == (
            envelope["partial_text"] or ""
        )
    p.provider.allow_failure_cases = False
    assert (
        await p.provider.generate_text(p.request, None, {"student_total": 2})
    ).error_code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "mutation",
    [
        "manifest_version",
        "contract_version",
        "selection_tuple",
        "unknown_manifest",
        "unknown_envelope",
        "duplicate_manifest",
        "duplicate_envelope",
        "hash",
        "missing_key",
        "branch",
        "path_escape",
        "absolute_path",
        "symlink",
        "duplicate_file",
        "phase",
    ],
)
async def test_manifest_and_lookup_fail_closed(prepared, tmp_path, mutation):
    p = prepared
    original_selection = p.selection
    if mutation == "selection_tuple":
        p.request = replace(
            p.request,
            demo_selection=replace(p.selection, content_version="stale"),
        )
    else:
        if mutation in ("manifest_version", "contract_version"):
            p.manifest[mutation] = "stale"
        elif mutation == "unknown_manifest":
            p.manifest["unknown"] = True
        elif mutation in ("unknown_envelope", "branch", "phase"):
            envelope = dict(p.envelope)
            envelope[
                {
                    "unknown_envelope": "extra",
                    "branch": "branch",
                    "phase": "analysis_phase",
                }[mutation]
            ] = {"unknown_envelope": True, "branch": "B", "phase": "single"}[
                mutation
            ]
            p.configure(envelope)
        elif mutation == "hash":
            (p.root / "response.json").write_bytes(b"corrupt")
        elif mutation == "missing_key":
            p.manifest["responses"] = {}
        elif mutation in ("path_escape", "absolute_path"):
            path = (
                "../outside.json"
                if mutation == "path_escape"
                else str(tmp_path / "outside.json")
            )
            p.manifest["files"][0]["path"] = path
            p.manifest["responses"][p.envelope["case_id"]] = path
        elif mutation == "duplicate_file":
            p.manifest["files"].append(p.manifest["files"][0])
        elif mutation == "symlink":
            outside = tmp_path / "outside.json"
            outside.write_bytes((p.root / "response.json").read_bytes())
            (p.root / "response.json").unlink()
            (p.root / "response.json").symlink_to(outside)
        elif mutation == "duplicate_envelope":
            raw = encode(p.envelope)[:-1] + b',"role":"student"}'
            (p.root / "response.json").write_bytes(raw)
            p.manifest["files"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        if mutation in ("phase", "branch"):
            p.request = replace(p.request, demo_selection=original_selection)
        raw = encode(p.manifest)
        if mutation == "duplicate_manifest":
            raw = raw[:-1] + b',"manifest_version":1}'
        (p.root / "manifest.json").write_bytes(raw)
        p.request = replace(
            p.request,
            demo_selection=replace(
                p.request.demo_selection,
                manifest_sha256=hashlib.sha256(raw).hexdigest(),
            ),
        )
    event = await p.provider.generate_text(
        p.request, None, {"student_total": 2}
    )
    assert event.type == "error" and event.error_code == "VERSION_MISMATCH"


async def test_unsupported_configuration_and_server_provenance(prepared):
    p = prepared
    assert p.provider.provenance(p.manifest) == dict(
        mode="demo",
        response_source="prepared_example",
        external_ai_called=False,
        contract_version="d0-03-v1-r3",
        content_version="d0-02-v1",
        fixture_version="demo-fixture-v1",
        record_origin="visitor_generated",
    )
    p.provider.session_id = 1
    assert p.provider.provenance(p.manifest)["record_origin"] == "seed_prepared"
    p.provider.session_id = 2
    p.provider.supported_config_sha256 = lambda version, config: "0" * 64
    assert (
        await p.provider.generate_text(p.request, None, {"student_total": 2})
    ).error_code == "DEMO_UNSUPPORTED_CONFIGURATION"


async def test_deadline_and_current_lease_checked_between_chunks(prepared):
    p = prepared
    p.provider.clock = lambda: 1
    deadline = SimpleNamespace(ends=2, first_ends=1)
    event = await p.provider.generate_text(
        p.request, None, {}, deadline=deadline
    )
    assert (
        event.error_code == "timeout_first_output"
        and not event.response_received
    )
    deadline.first_ends = None
    stream = p.provider.stream_text(p.request, None, {}, deadline=deadline)
    assert (await anext(stream)).type == "text_delta"
    deadline.ends = 1
    terminal = await anext(stream)
    assert terminal.error_code == "timeout_total" and terminal.response_received
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    deadline.ends = 2
    stream = p.provider.stream_text(p.request, None, {}, deadline=deadline)
    assert (await anext(stream)).type == "text_delta"
    p.visitors.stale = True
    terminal = await anext(stream)
    assert (
        terminal.type == "error"
        and terminal.error_code == "DEMO_STALE_GENERATION"
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_structured_is_explicitly_unconnected_in_first_stack(prepared):
    event = await prepared.provider.generate_structured(
        prepared.request, None, {}
    )
    assert event.type == "error" and event.structured is None


async def test_cancellation_produces_one_interrupted_terminal(prepared):
    import asyncio

    stream = prepared.provider.stream_text(
        prepared.request, None, {"student_total": 2}
    )
    assert (await anext(stream)).type == "text_delta"
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    task.cancel()
    event = await task
    assert event.type == "interrupted" and event.error_code == "interrupted"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def provider_turn(
    p,
    *,
    status="completed",
    owner=1,
    finished="2026-10-18 00:00:02",
    student_time="2026-10-18 00:00:02",
):
    from sqlalchemy import text

    turn = str(uuid4())
    async with p.factory() as db:
        await db.execute(
            text(
                'INSERT INTO generation_run VALUES (:run, 2, :turn, "student", :status, "message", :owner, :finished)'
            ),
            dict(
                run=turn,
                turn=turn,
                status=status,
                owner=owner,
                finished=finished,
            ),
        )
        await db.execute(
            text(
                'INSERT INTO message VALUES (1, 2, "teacher", :content, :turn, 1, NULL, "2026-10-18 00:00:01")'
            ),
            dict(content=INPUTS["A"], turn=turn),
        )
        await db.execute(
            text(
                'INSERT INTO message VALUES (2, 2, "student", "허구 완료 응답", :turn, 1, :turn, :time)'
            ),
            dict(turn=turn, time=student_time),
        )
        await db.commit()
    return turn


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_provider_progress_uses_only_real_completed_pairs(
    prepared, status
):
    p = prepared
    await provider_turn(p, status=status)
    if status == "completed":
        envelope = dict(
            p.envelope,
            case_id="normal/student/after_first/A/common/none/none",
            content_state="after_first",
            input_kind="common",
        )
        p.configure(envelope)
        p.request = replace(
            p.request, messages=[dict(role="user", content=INPUTS["common"])]
        )
    assert (
        await p.provider.generate_text(p.request, None, {"student_total": 2})
    ).type == "completed"


@pytest.mark.parametrize(
    "changes",
    [
        dict(owner=99),
        dict(finished=None),
        dict(student_time="2026-10-17 00:00:00"),
    ],
)
async def test_invalid_completed_history_cannot_select_examples(
    prepared, changes
):
    p = prepared
    await provider_turn(p, **changes)
    event = await p.provider.generate_text(
        p.request, None, {"student_total": 2}
    )
    assert event.type == "error" and event.error_code == "invalid_reference"


async def test_total_deadline_cancellation_keeps_timeout_code(prepared):
    import asyncio

    from src.services.call_policy import CallDeadline

    class BlockedVisitors:
        async def check(self, context):
            await asyncio.Event().wait()

    p = prepared
    p.provider.visitors = BlockedVisitors()
    deadline = CallDeadline({"student_total": 0.01}, "student")
    async with deadline.total():
        result = await p.provider.generate_text(
            p.request, None, {}, deadline=deadline
        )
    assert result.type == "error" and result.error_code == "timeout_total"


@pytest.mark.parametrize("streaming", [False, True])
async def test_owned_deadline_bounds_preparation(prepared, streaming):
    import asyncio

    class WaitingVisitors:
        async def check(self, context):
            await asyncio.Event().wait()

    p = prepared
    p.provider.visitors = WaitingVisitors()

    async def invoke():
        if streaming:
            return [
                event
                async for event in p.provider.stream_text(
                    p.request, None, {"student_total": 0.01}
                )
            ]
        return [
            await p.provider.generate_text(
                p.request, None, {"student_total": 0.01}
            )
        ]

    events = await asyncio.wait_for(invoke(), timeout=0.1)
    assert [(event.type, event.error_code) for event in events] == [
        ("error", "timeout_total")
    ]
    assert not events[0].response_received


async def test_final_currentness_check_cannot_outlive_deadline(prepared):
    p = prepared
    p.configure(dict(p.envelope, text="짧은 응답"))
    now = [1]
    p.provider.clock = lambda: now[0]
    deadline = SimpleNamespace(ends=2, first_ends=None)
    events = p.provider.stream_text(p.request, None, {}, deadline=deadline)
    assert (await anext(events)).type == "text_delta"
    original_check = p.visitors.check

    async def finish_check(context):
        await original_check(context)
        now[0] = 3

    p.visitors.check = finish_check
    terminal = await anext(events)
    assert terminal.type == "error" and terminal.error_code == "timeout_total"
    assert terminal.response_received
    with pytest.raises(StopAsyncIteration):
        await anext(events)


async def test_owned_deadline_bounds_final_await(prepared):
    import asyncio

    p = prepared
    p.configure(dict(p.envelope, text="짧은 응답"))

    class WaitingVisitors:
        async def check(self, context):
            await asyncio.Event().wait()

    async def invoke():
        events = []
        async for event in p.provider.stream_text(
            p.request, None, {"student_total": 0.1}
        ):
            events.append(event)
            if event.type == "text_delta":
                p.provider.visitors = WaitingVisitors()
        return events

    events = await asyncio.wait_for(invoke(), timeout=0.5)
    assert [event.type for event in events] == ["text_delta", "error"]
    assert events[-1].error_code == "timeout_total"
    assert events[-1].response_received


async def test_slow_consumer_receives_timeout_terminal_without_cancellation(
    prepared,
):
    import asyncio
    from contextlib import aclosing

    p = prepared
    p.configure(dict(p.envelope, text="짧은 응답"))
    events = []
    async with asyncio.timeout(1) as watchdog:
        try:
            async with aclosing(
                p.provider.stream_text(p.request, None, {"student_total": 0.2})
            ) as stream:
                async for event in stream:
                    events.append(event)
                    if event.type == "text_delta":
                        await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pytest.fail("owned stream deadline cancelled the consumer wait")
    assert not watchdog.expired()
    assert [(event.type, event.error_code) for event in events] == [
        ("text_delta", None),
        ("error", "timeout_total"),
    ]
    assert events[-1].response_received

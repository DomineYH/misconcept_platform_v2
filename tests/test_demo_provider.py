"""Prepared-provider boundaries, with fictional content confined to this file."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_demo_d1_01_visitors import ready_context
from test_demo_d1_01_visitors import visitors as binding_visitors

from src.demo.database import DemoError
from src.demo.output_binding import bind_output
from src.demo.provider import (
    DemoProvider,
    Selection,
    fixture_lookup_key,
    select_content,
)
from src.demo.visitors import DemoContext
from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.invocation_types import (
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.role_output_contracts import MentorOutput

visitors = binding_visitors


@dataclass(frozen=True)
class DemoStructuredRequest(StructuredRequest):
    demo_context: object = field(default=None, kw_only=True)
    demo_selection: object = field(default=None, kw_only=True)


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


async def test_structured_rejects_a_text_request(prepared):
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


BINDING_INPUTS = dict(
    A="Fictional first A?",
    B="Fictional first B?",
    C="Fictional first C?",
    common="Fictional compare?",
)


def binding_selection(**changes):
    values = dict(
        role="analysis",
        branch="A",
        content_state="after_compare",
        input_kind="common",
        trigger=None,
        analysis_phase="single",
        target_turn_id=None,
        fixture_case="normal",
    )
    return SimpleNamespace(**(values | changes))


def binding_template():
    return dict(
        schema_version=2,
        message_classifications=[
            dict(
                message_id="@teacher_first",
                quote=BINDING_INPUTS["A"],
                disposition="classified",
                rubric_id="r1",
                reason="Fictional classification.",
            ),
            dict(
                message_id="@teacher_common",
                quote=BINDING_INPUTS["common"],
                disposition="classified",
                rubric_id="r3",
                reason="Fictional classification.",
            ),
        ],
        misconception_findings=[
            dict(
                kind="changed",
                claim="Fictional observation.",
                evidence=[
                    dict(message_id="@student_first", quote="first answer"),
                    dict(message_id="@student_common", quote="second answer"),
                ],
            )
        ],
        brief_feedback=["Fictional feedback @teacher_first"],
        strengths=[
            dict(
                message_id="@teacher_first",
                quote=BINDING_INPUTS["A"],
                reason="Fictional strength.",
            )
        ],
        improvements=[],
        dialogue_coaching=[],
    )


async def seed_binding(ctx, visitors, *, first="A"):
    async with ctx.factory() as db, visitors.write_guard(ctx):
        for statement in (
            "INSERT INTO scenario(id,title,is_active,status,config_schema_version,config_version,config_json,review_required,review_reasons,created_at) VALUES(1,'fictional',1,'published',1,1,'{}',0,'[]','2026-10-18')",
            "INSERT INTO session(id,scenario_id,teacher_id,started_at,ended_at,tutor_intervention_count,tutor_question_count) VALUES(1,1,2,'2026-10-18','2026-10-19',0,0)",
            "INSERT INTO session(id,scenario_id,teacher_id,started_at,ended_at,tutor_intervention_count,tutor_question_count) VALUES(2,1,2,'2026-10-18','2026-10-19',0,0)",
        ):
            await db.execute(text(statement))
        for index, content in enumerate(
            [
                BINDING_INPUTS[first],
                BINDING_INPUTS["common"],
                "Fictional unsupported",
            ],
            1,
        ):
            await db.execute(
                text(
                    "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at,finished_at) VALUES(:id,2,1,:turn,'student',:id,'hash','hash','openai','fictional','completed','message',:start,:finish)"
                ),
                dict(
                    id=f"run{index}",
                    turn=f"turn{index}",
                    start=f"2026-10-18 00:00:0{index * 2 - 1}",
                    finish=f"2026-10-18 00:00:0{index * 2}",
                ),
            )
            await db.execute(
                text(
                    "INSERT INTO message(id,session_id,turn_id,turn_index,role,content,created_at) VALUES(:id,1,:turn,:index,'teacher',:content,:time)"
                ),
                dict(
                    id=index * 2 - 1,
                    turn=f"turn{index}",
                    index=index,
                    content=content,
                    time=f"2026-10-18 00:00:0{index * 2 - 1}",
                ),
            )
            await db.execute(
                text(
                    "INSERT INTO message(id,session_id,turn_id,turn_index,role,content,generation_run_id,created_at) VALUES(:id,1,:turn,:index,'student',:content,:run,:time)"
                ),
                dict(
                    id=index * 2,
                    turn=f"turn{index}",
                    index=index,
                    content=[
                        "Fictional first answer",
                        "Fictional second answer",
                        "Fictional unsupported answer",
                    ][index - 1],
                    run=f"run{index}",
                    time=f"2026-10-18 00:00:0{index * 2}",
                ),
            )
        await db.commit()
    return await binding_validation(ctx)


async def binding_validation(ctx):
    async with ctx.factory() as db:
        messages = (
            (
                await db.execute(
                    text(
                        "SELECT id,role,content FROM message WHERE session_id=1 ORDER BY created_at,id"
                    )
                )
            )
            .mappings()
            .all()
        )
    return dict(
        messages=[dict(m) for m in messages],
        labels={"r1": "one", "r3": "three"},
        classification_enabled=True,
        actor_id=ctx.user_id,
    )


async def call_binding(ctx, visitors, validation, template=None, **selection):
    return await bind_output(
        binding_template() if template is None else template,
        context=ctx,
        visitors=visitors,
        session_id=1,
        selection=binding_selection(**selection),
        validation_context=validation,
        inputs=BINDING_INPUTS,
    )


async def test_binding_uses_completed_current_database_and_preserves_quotes(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    result = await call_binding(ctx, visitors, validation, template)
    assert [c["message_id"] for c in result["message_classifications"]] == [
        1,
        3,
        5,
    ]
    assert (
        result["message_classifications"][-1]["disposition"] == "non_analyzable"
    )
    assert result["message_classifications"][-1]["rubric_id"] is None
    assert result["brief_feedback"] == template["brief_feedback"]
    assert result["misconception_findings"][0]["evidence"] == [
        dict(message_id=2, quote="first answer"),
        dict(message_id=4, quote="second answer"),
    ]
    assert template == binding_template()
    assert UnifiedAnalysisOutput.model_validate(
        result, strict=True, context=validation
    )
    assert validation["analysis_errors"] == []
    assert visitors.lease_count(ctx.scope) == 1
    await visitors.release_lease(ctx.lease)


@pytest.mark.parametrize(
    "mutation",
    [
        "quote",
        "role",
        "time",
        "session",
        "incomplete",
        "numeric",
        "reversed",
        "actor",
        "missing_input",
        "foreign_factory",
    ],
)
async def test_binding_rejects_invalid_reference(visitors, mutation):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    if mutation == "quote":
        template["strengths"][0]["quote"] = "Other database quote"
    elif mutation == "numeric":
        template["strengths"][0]["message_id"] = 1
    elif mutation == "reversed":
        template["misconception_findings"][0]["evidence"].reverse()
    elif mutation == "actor":
        validation["actor_id"] = 1
    elif mutation == "missing_input":
        validation["messages"].pop()
    elif mutation == "foreign_factory":
        _, _, other = await ready_context(visitors, "teacher")
        validation_b = await seed_binding(other, visitors, first="B")
        assert [m["id"] for m in validation["messages"]] == [
            m["id"] for m in validation_b["messages"]
        ]
        ctx = replace(ctx, factory=other.factory)
    else:
        statement = dict(
            role="UPDATE message SET role='tutor' WHERE id=2",
            time="UPDATE message SET created_at='2026-10-17' WHERE id=4",
            session="UPDATE message SET session_id=2 WHERE id=2",
            incomplete="UPDATE generation_run SET status='interrupted' WHERE id='run1'",
        )[mutation]
        async with ctx.factory() as db, visitors.write_guard(ctx):
            await db.execute(text(statement))
            await db.commit()
    with pytest.raises((InvocationError, DemoError)) as error:
        await call_binding(ctx, visitors, validation, template)
    assert str(error.value) in {"invalid_reference", "DEMO_INVALID_REQUEST"}


async def test_binding_chunk_projection_keeps_only_owned_and_valid_changed(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    validation["owned_message_ids"] = [3, 4]
    validation["messages"] = validation["messages"][:4]
    template = binding_template()
    template["misconception_findings"].append(
        dict(
            kind="maintained",
            claim="Fictional overlap only",
            evidence=[dict(message_id="@student_first", quote="first answer")],
        )
    )
    result = await call_binding(
        ctx, visitors, validation, template, analysis_phase="chunk"
    )
    assert [c["message_id"] for c in result["message_classifications"]] == [3]
    assert result["strengths"] == []
    assert len(result["misconception_findings"]) == 1
    assert result["misconception_findings"][0]["kind"] == "changed"
    validation["messages"] = validation["messages"][2:]
    result = await call_binding(
        ctx, visitors, validation, template, analysis_phase="chunk"
    )
    assert result["misconception_findings"] == []


async def test_binding_merge_uses_exact_sections_and_verified_evidence(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    template.pop("message_classifications")
    validation["allowed_evidence"] = [
        dict(message_id=2, quote="first answer"),
        dict(message_id=4, quote="second answer"),
    ]
    result = await call_binding(
        ctx, visitors, validation, template, analysis_phase="merge"
    )
    assert set(result) == set(template)
    assert result["strengths"] == []
    assert len(result["misconception_findings"]) == 1
    validation["allowed_evidence"].pop()
    assert (
        await call_binding(
            ctx, visitors, validation, template, analysis_phase="merge"
        )
    )["misconception_findings"] == []
    template["findings"] = []
    with pytest.raises(InvocationError, match="invalid_output"):
        await call_binding(
            ctx, visitors, validation, template, analysis_phase="merge"
        )


async def test_binding_mentor_requires_completed_target_and_analysis_ended(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    mentor = dict(
        should_intervene=False, feedback="", reason_summary="Fictional reason."
    )
    assert (
        await call_binding(
            ctx,
            visitors,
            {},
            mentor,
            role="mentor",
            target_turn_id="turn2",
            analysis_phase="none",
        )
        == mentor
    )
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(
            ctx,
            visitors,
            {},
            mentor,
            role="mentor",
            target_turn_id="foreign",
            analysis_phase="none",
        )
    async with ctx.factory() as db, visitors.write_guard(ctx):
        await db.execute(text("UPDATE session SET ended_at=NULL WHERE id=1"))
        await db.commit()
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(ctx, visitors, validation)


async def test_binding_admin_checks_actual_actor_and_teacher_session_ownership(
    visitors,
):
    _, _, admin = await ready_context(visitors, "admin")
    validation = await seed_binding(admin, visitors)
    assert await call_binding(admin, visitors, validation)
    async with admin.factory() as db, visitors.write_guard(admin):
        await db.execute(text("UPDATE user SET role='teacher' WHERE id=1"))
        await db.commit()
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(admin, visitors, validation)


@pytest.mark.parametrize(
    "case", ["missing_classification", "duplicate_classification"]
)
async def test_binding_preserves_strict_coverage_failures(visitors, case):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    if case == "missing_classification":
        template["message_classifications"].pop()
    else:
        template["message_classifications"].append(
            dict(template["message_classifications"][0])
        )
    result = await call_binding(
        ctx, visitors, validation, template, fixture_case=case
    )
    UnifiedAnalysisOutput.model_validate(
        result, strict=True, context=validation
    )
    assert validation["analysis_errors"]
    assert all(c["message_id"] != 5 for c in result["message_classifications"])


async def test_binding_checks_lease_again_after_database_access(
    visitors, monkeypatch
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    check = visitors.check
    calls = []

    async def revoke(context):
        calls.append(context)
        if len(calls) == 2:
            await visitors.release_lease(context.lease)
        return await check(context)

    monkeypatch.setattr(visitors, "check", revoke)
    with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
        await call_binding(ctx, visitors, validation)
    assert calls == [ctx, ctx]
    assert visitors.lease_count(ctx.scope) == 0


async def test_binding_same_primary_keys_do_not_trust_other_database_content(
    visitors,
):
    _, _, first = await ready_context(visitors, "teacher")
    _, _, second = await ready_context(visitors, "teacher")
    validation = await seed_binding(first, visitors)
    foreign = await seed_binding(second, visitors, first="B")
    assert [m["id"] for m in validation["messages"]] == [
        m["id"] for m in foreign["messages"]
    ]
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(first, visitors, foreign)
    template = binding_template()
    template["message_classifications"][0]["quote"] = BINDING_INPUTS["B"]
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(first, visitors, validation, template)


async def test_binding_chunk_filters_each_section_and_unsupported_evidence(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    validation["owned_message_ids"] = [5, 6]
    template = binding_template()
    template["improvements"] = [
        dict(
            message_id="@student_first",
            quote="first answer",
            missed_reason="Fictional missed clue",
            alternative_question="Fictional question?",
            alternative_reason="Fictional reason",
        )
    ]
    template["dialogue_coaching"] = [
        dict(
            message_id="@teacher_common",
            quote=BINDING_INPUTS["common"],
            role="teacher",
            marker="key_clue",
            note="Fictional note",
        )
    ]
    result = await call_binding(
        ctx, visitors, validation, template, analysis_phase="chunk"
    )
    assert [c["message_id"] for c in result["message_classifications"]] == [5]
    for section in (
        "misconception_findings",
        "strengths",
        "improvements",
        "dialogue_coaching",
    ):
        assert result[section] == []
    UnifiedAnalysisOutput.model_validate(
        result, strict=True, context=validation
    )
    assert validation["analysis_errors"] == []
    template["misconception_findings"] = [
        dict(
            kind="maintained",
            claim="Fictional claim",
            evidence=[
                dict(
                    message_id="@unsupported_student:1",
                    quote="unsupported answer",
                )
            ],
        )
    ]
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(
            ctx, visitors, validation, template, analysis_phase="chunk"
        )


async def test_binding_wrong_unsupported_classification_is_rejected(visitors):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    template["message_classifications"].append(
        dict(
            message_id="@unsupported_teacher:1",
            quote="Fictional unsupported",
            disposition="classified",
            rubric_id="r1",
            reason="Fictional incorrect classification",
        )
    )
    with pytest.raises(InvocationError, match="invalid_reference"):
        await call_binding(ctx, visitors, validation, template)


async def test_binding_partial_retains_valid_sections_for_strict_validator(
    visitors,
):
    _, _, ctx = await ready_context(visitors, "teacher")
    validation = await seed_binding(ctx, visitors)
    template = binding_template()
    template["message_classifications"][0]["quote"] = "Fictional altered quote"
    result = await call_binding(
        ctx, visitors, validation, template, fixture_case="analysis_partial"
    )
    assert result["message_classifications"][0]["message_id"] == 1
    validated = UnifiedAnalysisOutput.model_validate(
        result, strict=True, context=validation
    ).model_dump()
    assert [c["message_id"] for c in validated["message_classifications"]] == [
        3
    ]
    assert validated["misconception_findings"]
    assert validated["strengths"]
    assert validation["validated_analysis"] == validated
    assert (
        dict(section="message_classifications", code="invalid_reference")
        in validation["analysis_errors"]
    )


async def structured_prepared(
    p,
    visitors,
    *,
    role="analysis",
    phase="single",
    case="normal",
    trigger=None,
    target=None,
    value=None,
    branch="A",
    content_state="after_compare",
    input_kind="common",
):
    _, _, context = await ready_context(visitors, "teacher")
    validation = await seed_binding(context, visitors, first=branch)
    if role == "mentor":
        validation = dict(trigger=trigger)
    key = f'{case}/{role}/{content_state}/{branch}/{input_kind}/{trigger or "none"}/{phase}'
    envelope = dict(
        p.envelope,
        case_id=key,
        role=role,
        content_state=content_state,
        branch=branch,
        input_kind=input_kind,
        analysis_phase=phase,
        trigger=trigger,
        text=None,
        structured=binding_template() if value is None else value,
    )
    if case == "analysis_failed":
        envelope.update(
            terminal="error", error_code="invalid_output", structured=None
        )
    p.configure(envelope)
    p.provider.visitors = visitors
    p.provider.inputs = BINDING_INPUTS
    p.provider.session_id = 1
    p.provider.allow_failure_cases = case != "normal"
    selection = replace(p.selection, target_turn_id=target)
    request = DemoStructuredRequest(
        "demo",
        "demo-classroom-v1",
        role,
        "Fictional instruction",
        [],
        {"max_output_tokens": 4096},
        str(uuid4()),
        MentorOutput if role == "mentor" else UnifiedAnalysisOutput,
        validation,
        demo_context=context,
        demo_selection=selection,
    )
    return request, context


async def test_structured_analysis_connects_real_binding_and_strict_validation(
    prepared, visitors
):
    p = prepared
    request, _ = await structured_prepared(p, visitors)
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "completed" and result.text == ""
    assert [
        c["message_id"] for c in result.structured["message_classifications"]
    ] == [1, 3, 5]
    assert request.validation_context["analysis_errors"] == []
    assert request.validation_context["validated_analysis"] == result.structured
    assert (
        result.usage["total_tokens"] is None
        and not result.usage["usage_complete"]
    )


async def test_c_single_analysis_uses_real_classifications(prepared, visitors):
    p = prepared
    template = binding_template()
    template["message_classifications"][0]["quote"] = BINDING_INPUTS["C"]
    template["strengths"][0]["quote"] = BINDING_INPUTS["C"]
    request, _ = await structured_prepared(
        p, visitors, branch="C", value=template
    )
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "completed"
    assert [
        (c["message_id"], c["disposition"], c["rubric_id"])
        for c in result.structured["message_classifications"]
    ] == [
        (1, "classified", "r1"),
        (3, "classified", "r3"),
        (5, "non_analyzable", None),
    ]
    assert request.validation_context["analysis_errors"] == []


async def test_structured_merge_preserves_verified_sections(prepared, visitors):
    p = prepared
    template = binding_template()
    template.pop("message_classifications")
    request, _ = await structured_prepared(
        p, visitors, phase="merge", value=template
    )
    request = replace(request, output_schema=MergeAnalysisOutput)
    request.validation_context["allowed_evidence"] = [
        dict(message_id=2, quote="first answer"),
        dict(message_id=4, quote="second answer"),
        dict(message_id=1, quote=BINDING_INPUTS["A"]),
    ]
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "completed"
    assert set(result.structured) == {
        "schema_version",
        "misconception_findings",
        "brief_feedback",
        "strengths",
        "improvements",
        "dialogue_coaching",
    }
    assert result.structured["misconception_findings"][0]["evidence"] == [
        dict(message_id=2, quote="first answer"),
        dict(message_id=4, quote="second answer"),
    ]
    assert result.structured["strengths"][0]["message_id"] == 1
    assert request.validation_context["analysis_errors"] == []


async def test_analysis_partial_retains_validated_sections_and_error(
    prepared, visitors
):
    p = prepared
    template = binding_template()
    template["strengths"][0]["quote"] = "absent quote"
    request, _ = await structured_prepared(
        p, visitors, case="analysis_partial", value=template
    )
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "error" and result.error_code == "invalid_reference"
    assert request.validation_context["validated_analysis"]["strengths"] == []
    assert request.validation_context["validated_analysis"][
        "misconception_findings"
    ]
    assert request.validation_context["analysis_errors"]


async def test_structured_schema_phase_mismatch_fails_before_binding(
    prepared, visitors
):
    p = prepared
    request, _ = await structured_prepared(p, visitors)
    result = await p.provider.generate_structured(
        replace(request, output_schema=MentorOutput),
        None,
        {"analysis_total": 2},
    )
    assert result.type == "error" and result.error_code == "VERSION_MISMATCH"


@pytest.mark.parametrize(
    "trigger,intervene", [("auto", False), ("manual", True)]
)
async def test_mentor_common_turn_uses_trigger_and_real_target(
    prepared, visitors, trigger, intervene
):
    p = prepared
    target = str(uuid4())
    value = dict(
        should_intervene=intervene,
        feedback="Fictional coaching" if intervene else "",
        reason_summary="Fictional private reason",
    )
    request, context = await structured_prepared(
        p,
        visitors,
        role="mentor",
        phase="none",
        trigger=trigger,
        target=target,
        value=value,
    )
    async with context.factory() as db, visitors.write_guard(context):
        await db.execute(
            text("UPDATE message SET turn_id=:turn WHERE turn_id='turn2'"),
            dict(turn=target),
        )
        await db.execute(
            text(
                "UPDATE generation_run SET turn_id=:turn WHERE turn_id='turn2'"
            ),
            dict(turn=target),
        )
        await db.commit()
    result = await p.provider.generate_structured(
        request, None, {"mentor_total": 2}
    )
    assert result.type == "completed" and result.structured == value


@pytest.mark.parametrize("branch", ["A", "B", "C"])
@pytest.mark.parametrize(
    "target_number,state,kind,expected",
    [
        (1, "after_first", "first", True),
        (3, "after_compare", "unsupported", False),
    ],
)
async def test_mentor_first_and_unsupported_auto_contract(
    prepared, visitors, branch, target_number, state, kind, expected
):
    p = prepared
    target = str(uuid4())
    value = dict(
        should_intervene=expected,
        feedback="Fictional coaching" if expected else "",
        reason_summary="Fictional reason",
    )
    request, context = await structured_prepared(
        p,
        visitors,
        role="mentor",
        phase="none",
        trigger="auto",
        target=target,
        value=value,
        branch=branch,
        content_state=state,
        input_kind=branch if kind == "first" else kind,
    )
    async with context.factory() as db, visitors.write_guard(context):
        await db.execute(
            text("UPDATE message SET turn_id=:turn WHERE turn_id=:old"),
            dict(turn=target, old=f"turn{target_number}"),
        )
        await db.execute(
            text("UPDATE generation_run SET turn_id=:turn WHERE turn_id=:old"),
            dict(turn=target, old=f"turn{target_number}"),
        )
        await db.commit()
    result = await p.provider.generate_structured(
        request, None, {"mentor_total": 2}
    )
    assert result.type == "completed" and result.structured == value


@pytest.mark.parametrize(
    "owned,expected_rubric", [([1, 2], "r2"), ([3, 4], "r3")]
)
async def test_b_analysis_chunk_has_exact_owned_rubric_and_projection(
    prepared, visitors, owned, expected_rubric
):
    p = prepared
    template = binding_template()
    template["message_classifications"][0].update(
        quote=BINDING_INPUTS["B"], rubric_id="r2"
    )
    template["strengths"][0]["quote"] = BINDING_INPUTS["B"]
    request, _ = await structured_prepared(
        p, visitors, phase="chunk", branch="B", value=template
    )
    validation = request.validation_context
    validation["labels"]["r2"] = "two"
    validation["owned_message_ids"] = owned
    validation["messages"] = validation["messages"][: owned[-1]]
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "completed"
    assert [
        (c["message_id"], c["rubric_id"])
        for c in result.structured["message_classifications"]
    ] == [(owned[0], expected_rubric)]
    assert validation["analysis_errors"] == []
    assert len(result.structured["misconception_findings"]) == (
        0 if owned == [1, 2] else 1
    )


async def test_analysis_failed_has_no_adoptable_result(prepared, visitors):
    p = prepared
    request, _ = await structured_prepared(p, visitors, case="analysis_failed")
    result = await p.provider.generate_structured(
        request, None, {"analysis_total": 2}
    )
    assert result.type == "error" and result.error_code == "invalid_output"
    assert (
        result.structured is None
        and "validated_analysis" not in request.validation_context
    )


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


async def test_structured_total_deadline_cancellation_keeps_timeout_code(
    prepared, visitors
):
    import asyncio

    from src.services.call_policy import CallDeadline

    class BlockedVisitors:
        async def check(self, context):
            await asyncio.Event().wait()

    p = prepared
    request, _ = await structured_prepared(p, visitors)
    p.provider.visitors = BlockedVisitors()
    deadline = CallDeadline({"analysis_total": 0.01}, "analysis")
    async with deadline.total():
        result = await p.provider.generate_structured(
            request, None, {}, deadline=deadline
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


async def test_owned_text_deadline_bounds_real_visitor_lock(prepared, visitors):
    import asyncio

    p = prepared
    _, _, context = await ready_context(visitors, "teacher")
    p.provider.visitors = visitors
    request = replace(p.request, demo_context=context)
    try:
        async with visitors.transition(context.verified_visitor):
            async with asyncio.timeout(0.1) as watchdog:
                result = await asyncio.create_task(
                    p.provider.generate_text(
                        request, None, {"student_total": 0.01}
                    )
                )
    finally:
        await visitors.release_lease(context.lease)
    assert result.type == "error" and result.error_code == "timeout_total"
    assert not result.response_received
    assert not watchdog.expired()


async def test_owned_structured_deadline_bounds_real_visitor_lock(
    prepared, visitors
):
    import asyncio

    p = prepared
    request, context = await structured_prepared(p, visitors)
    try:
        async with visitors.transition(context.verified_visitor):
            async with asyncio.timeout(0.1) as watchdog:
                result = await asyncio.create_task(
                    p.provider.generate_structured(
                        request, None, {"analysis_total": 0.01}
                    )
                )
    finally:
        await visitors.release_lease(context.lease)
    assert result.type == "error" and result.error_code == "timeout_total"
    assert not result.response_received
    assert not watchdog.expired()

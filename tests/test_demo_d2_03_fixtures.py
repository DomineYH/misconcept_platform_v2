"""D2-03 prepared fixtures: D0-02 text, manifest tuple and real provider lookup."""

import hashlib
import json
import shutil
from dataclasses import dataclass, field, replace
from functools import cache
from itertools import product
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_demo_d1_01_visitors import ready_context
from test_demo_d1_01_visitors import visitors as fixture_visitors

from src.api.schemas.scenario_config import ScenarioConfig
from src.demo.provider import (
    FAILURES,
    DemoProvider,
    Selection,
    fixture_lookup_key,
    select_content,
)
from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.invocation_types import StructuredRequest, TextRequest
from src.services.role_output_contracts import MentorOutput

visitors = fixture_visitors
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src/demo/fixtures"
MANIFEST = FIXTURES / "manifest.json"
MIGRATIONS = ROOT / "src/db/migrations"
D0_02 = (ROOT / "docs/demo/d0-02-experience-spec.md").read_text()

# D0-02 §4/§5 originals; test_d0_02_text_is_quoted_verbatim pins them to the doc.
INPUTS = dict(
    A="1/8이 1/4보다 크다고 생각한 이유를 말해 줄래?",
    B="같은 크기의 종이띠에서는 1/4이 1/8보다 커. 이 답을 기억해 봐.",
    C="같은 크기의 종이띠를 4조각과 8조각으로 똑같이 나누면 한 조각은 어느 쪽이 더 클까? 지금 생각을 말해 줄래?",
    common="같은 크기의 종이띠 두 개를 각각 똑같이 4조각과 8조각으로 나누어 한 조각씩 겹쳐 보자. 어느 쪽 조각이 더 크고, 조각 수가 늘면 한 조각의 크기는 어떻게 될까?",
)
STUDENT = dict(
    A="8이 4보다 크니까 1/8 조각도 더 클 것 같아요.",
    B="답은 1/4이라고 들었지만, 8이 더 큰 수라서 왜 1/8이 더 작은지는 모르겠어요.",
    C="8조각이면 더 많으니까 한 조각도 1/8 쪽이 클 것 같아요. 직접 나누어 보지는 않았어요.",
    common="같은 크기의 종이띠라면 1/4 조각이 1/8 조각보다 커요. 조각 수가 많아지면 한 조각은 더 작아져요. 아까는 분모의 수만 비교했어요.",
    unsupported="이 데모는 준비된 예시 응답만 보여 줍니다. 지금은 같은 크기의 종이띠에서 1/4과 1/8을 비교하는 추천 입력을 선택해 주세요.",
)
COACHING = dict(
    A="학생봇이 분모의 크기로 판단하는 이유를 말했습니다. 다음에는 같은 크기의 종이띠를 등분했을 때 한 조각을 비교하게 해 보세요.",
    B="크기 관계를 알려 주었지만 학생봇은 이유를 설명하지 못했습니다. 같은 전체에서 등분 수와 한 조각의 크기를 연결해 묻는 질문을 해 보세요.",
    C="학생봇이 전체 조각 수와 한 조각의 크기를 연결해 생각하고 있습니다. 두 종이띠의 전체 크기가 같다는 조건을 유지하며 한 조각을 겹쳐 비교하게 해 보세요.",
)
MANUAL = "완료된 대상 턴에 대한 수동 도움 요청에 코칭한다."
MENTOR = {
    **{
        (x, "auto"): (True, COACHING[x], reason)
        for x, reason in (
            (
                "A",
                "분모로 크기를 판단하는 이유가 드러나 같은 전체 비교를 제안한다.",
            ),
            (
                "B",
                "정답을 들은 뒤에도 크기의 이유를 설명하지 못해 비교 질문을 제안한다.",
            ),
            (
                "C",
                "조각 수와 한 조각 크기를 연결하는 주장이 있어 직접 비교를 제안한다.",
            ),
        )
    },
    **{(x, "manual"): (True, COACHING[x], MANUAL) for x in "ABC"},
    ("common", "auto"): (
        False,
        "",
        "같은 전체와 한 조각 크기를 연결하는 표현이 나와 추가 자동 코칭을 생략한다.",
    ),
    ("common", "manual"): (
        True,
        "학생봇이 같은 전체에서 한 조각의 크기를 설명했습니다. 이 표현만으로 실제 이해를 확정하지 말고, 다음 연습에서는 다른 단위분수도 같은 방법으로 설명하도록 물어보세요.",
        MANUAL,
    ),
    ("unsupported", "auto"): (
        False,
        "",
        "지원 범위 밖 입력이므로 교육적 코칭을 생성하지 않는다.",
    ),
    ("unsupported", "manual"): (
        True,
        "이 입력은 준비된 예시 범위에 없습니다. 현재 단계의 추천 입력으로 돌아가 체험해 주세요.",
        "수동 도움에 지원 범위를 안내한다.",
    ),
}
UNSUPPORTED_INPUT = "오늘 날씨는 어때?"
LABELS = dict(r1="생각 확인", r2="정답 제시", r3="같은 전체의 근거 비교")
DB_SCHEMA_VERSION = "037_demo_provider.sql"
# Logical PKs the official installer plus seed builder produce (3 external
# placeholder connections precede the demo one); seed.py tests re-check them.
SEED_REFS = dict(
    admin=1,
    teacher=2,
    group=1,
    scenario=1,
    completed_session=1,
    model=1,
    connection=4,
)
SIX_KEYS = {
    "schema_version",
    "misconception_findings",
    "brief_feedback",
    "strengths",
    "improvements",
    "dialogue_coaching",
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


SUPPORTED_FIELDS = dict(
    problem=("public_text", "learning_objective"),
    student=(
        "name",
        "public_profile",
        "internal_profile",
        "misconception",
        "behavior_instruction",
    ),
    mentor=(
        "mode",
        "name",
        "welcome_message",
        "behavior_instruction",
        "intervention_policy",
    ),
    analysis=(
        "context",
        "expected_understanding",
        "instruction",
        "rubric_name",
        "rubric_description",
        "category_name",
        "classification_enabled",
        "rubric",
    ),
)


def _interim_supported_config_sha256(config_schema_version, config):
    """Contract §16.1 projection, test-only: the shared helper is #195 PR3's."""
    assert type(config_schema_version) is int and config_schema_version == 1
    raw = {section: dict(value) for section, value in config.items()}
    resolved = {
        role: raw[role].pop("resolved_model_config", None)
        for role in ("student", "mentor", "analysis")
    }
    full = ScenarioConfig.model_validate(raw).model_dump()
    projection = {
        section: {name: full[section][name] for name in names}
        for section, names in SUPPORTED_FIELDS.items()
    }
    for role, value in resolved.items():
        projection[role]["resolved_model_config"] = value and {
            k: value[k] for k in ("provider", "model_id")
        }
    return _sha(
        json.dumps(
            dict(config_schema_version=1, config=projection),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


# Swap for src.services.model_configuration.supported_config_sha256 after #195 PR3.
supported_config_sha256 = _interim_supported_config_sha256

SCENARIO = json.loads((FIXTURES / "seed/scenario.json").read_text())
DEMO_CONFIG = {
    section: (
        dict(
            value,
            resolved_model_config=dict(
                model_config_id=SEED_REFS["model"],
                provider_connection_id=SEED_REFS["connection"],
                provider="demo",
                model_id="demo-classroom-v1",
                options=dict(max_output_tokens=4096),
            ),
        )
        if section in ("student", "mentor", "analysis")
        else value
    )
    for section, value in SCENARIO["config"].items()
}


def migration_set_sha256():
    """Sorted POSIX path + NUL + bytes + NUL over what run_all_migrations installs."""
    paths = [MIGRATIONS / "baseline.sql"] + [
        path
        for path in MIGRATIONS.glob("[0-9]*.sql")
        if int(path.name.split("_")[0]) > 23
        and not path.name.endswith("_down.sql")
    ]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    assert (
        max(p.name for p in paths if p.name[0].isdigit()) == DB_SCHEMA_VERSION
    )
    return digest.hexdigest()


def expected_manifest():
    files, responses = [], {}
    for path in FIXTURES.rglob("*.json"):
        relative = path.relative_to(FIXTURES).as_posix()
        if relative == "manifest.json":
            continue
        kind = relative.split("/")[0]
        files.append(
            dict(path=relative, sha256=_sha(path.read_bytes()), kind=kind)
        )
        if kind != "seed":
            responses[json.loads(path.read_text())["case_id"]] = relative
    return dict(
        manifest_version=1,
        contract_version="d0-03-v1-r3",
        content_version="d0-02-v1",
        fixture_version="demo-fixture-v1",
        db_schema_version=DB_SCHEMA_VERSION,
        migration_set_sha256=migration_set_sha256(),
        scenario_config_schema_version=1,
        analysis_schema_version=2,
        provider="demo",
        model_id="demo-classroom-v1",
        capability_definition_version="demo-cap-v1",
        role_contract_versions=dict(
            student="s1-v1", mentor="s3-v1", analysis="s4-v2"
        ),
        scenario_ref="unit-fraction-v1",
        supported_config_sha256=supported_config_sha256(1, DEMO_CONFIG),
        seed_version="demo-seed-v1",
        seed_timestamp="2026-10-18T00:00:00Z",
        seed_refs=SEED_REFS,
        files=sorted(files, key=lambda item: item["path"]),
        lookup_version=1,
        responses=dict(sorted(responses.items())),
        seed_provenance=dict(
            source="synthetic",
            generator="src/demo/seed.py",
            content_version="d0-02-v1",
        ),
    )


def render_manifest():
    return json.dumps(expected_manifest(), ensure_ascii=False, indent=2) + "\n"


def responses():
    manifest = json.loads(MANIFEST.read_text())
    return {
        key: json.loads((FIXTURES / path).read_text())
        for key, path in manifest["responses"].items()
    }


def reachable_normal_keys():
    """Walk real histories through the provider's selector (contract §17.1)."""
    keys = set()
    texts = dict(INPUTS, unsupported=UNSUPPORTED_INPUT)
    for depth in range(4):
        for path in product(texts, repeat=depth):
            turns = [dict(teacher=dict(content=texts[k])) for k in path]
            state, branch, _ = select_content(turns, "", INPUTS)
            for phase in ("single", "chunk", "merge"):
                kind = "common" if state == "after_compare" else branch
                keys.add(
                    fixture_lookup_key(
                        selection(
                            "analysis",
                            state,
                            branch,
                            kind or "unsupported",
                            phase=phase,
                        )
                    )
                )
            for value in texts.values():
                state, branch, kind = select_content(turns, value, INPUTS)
                student_branch = kind if kind in ("A", "B", "C") else branch
                keys.add(
                    fixture_lookup_key(
                        selection("student", state, student_branch, kind)
                    )
                )
                if kind in ("A", "B", "C"):
                    state, branch = "after_first", kind
                elif kind == "common":
                    state = "after_compare"
                for trigger in ("auto", "manual"):
                    keys.add(
                        fixture_lookup_key(
                            selection(
                                "mentor", state, branch, kind, trigger=trigger
                            )
                        )
                    )
    return keys


@cache
def manifest_sha256():
    return _sha(MANIFEST.read_bytes())


def selection(role, state, branch, kind, *, trigger=None, phase=None, **more):
    return Selection(
        role=role,
        content_state=state,
        branch=branch,
        input_kind=kind,
        manifest_sha256=manifest_sha256(),
        trigger=trigger,
        target_turn_id=(
            more.pop("target_turn_id", str(uuid4()))
            if role == "mentor"
            else None
        ),
        analysis_phase=phase or "none",
        **more,
    )


def test_d0_02_text_is_quoted_verbatim():
    strings = [
        *INPUTS.values(),
        *STUDENT.values(),
        *(s for value in MENTOR.values() for s in value[1:] if s),
    ]
    assert all(s in D0_02 for s in strings)

    def leaves(value):
        if isinstance(value, dict):
            return [s for v in value.values() for s in leaves(v)]
        if isinstance(value, list):
            return [s for v in value for s in leaves(v)]
        return [value] if isinstance(value, str) else []

    assert all(s in D0_02 for s in leaves(SCENARIO))
    assert SCENARIO["config_schema_version"] == 1
    ScenarioConfig.model_validate(SCENARIO["config"])
    assert {
        item["id"]: item["name"]
        for item in SCENARIO["config"]["analysis"]["rubric"]
    } == LABELS


def test_supported_hash_matches_contract_vector():
    vector = dict(
        problem=dict(public_text="문제"),
        student=dict(behavior_instruction="지시"),
        mentor={},
        analysis={},
        runtime={},
    )
    assert (
        supported_config_sha256(1, vector)
        == "84c3988b5ab0c5ad9aa47e1c7c66edb755909e8b65a820eac9456bcf56dfaadb"
    )
    wider = json.loads(json.dumps(DEMO_CONFIG))
    for role in ("student", "mentor", "analysis"):
        wider[role]["resolved_model_config"].update(
            model_config_id=9, options=dict(max_output_tokens=8192)
        )
    assert supported_config_sha256(1, wider) == supported_config_sha256(
        1, DEMO_CONFIG
    )
    wider["analysis"]["resolved_model_config"]["model_id"] = "other"
    assert supported_config_sha256(1, wider) != supported_config_sha256(
        1, DEMO_CONFIG
    )


def test_committed_manifest_equals_regenerated():
    assert (
        MANIFEST.read_text() == render_manifest()
    ), "regenerate src/demo/fixtures/manifest.json from render_manifest()"


def test_lookup_covers_exactly_the_reachable_normal_keys():
    normal = {k for k in responses() if k.startswith("normal/")}
    assert normal == reachable_normal_keys()
    roles = [k.split("/")[1] for k in normal]
    assert [roles.count(r) for r in ("student", "mentor", "analysis")] == [
        13,
        26,
        21,
    ]
    failures = {k.split("/")[0] for k in responses()} - {"normal"}
    assert failures == set(FAILURES)


def test_envelopes_match_their_key_and_terminal_contract():
    names = ("role", "content_state", "branch", "input_kind", "trigger")
    for key, envelope in responses().items():
        case, role, state, branch, kind, trigger, phase = key.split("/")
        assert list(envelope) == [
            "fixture_version",
            "case_id",
            *names,
            "analysis_phase",
            "terminal",
            "error_code",
            "text",
            "structured",
            "partial_text",
        ]
        assert envelope["fixture_version"] == "demo-fixture-v1"
        assert envelope["case_id"] == key
        assert [envelope[n] for n in names] == [
            role,
            state,
            None if branch == "none" else branch,
            kind,
            None if trigger == "none" else trigger,
        ]
        assert envelope["analysis_phase"] == phase
        assert (envelope["terminal"], envelope["error_code"]) == FAILURES.get(
            case, ("completed", None)
        )
        if case == "normal" and role == "student":
            assert envelope["text"] and envelope["structured"] is None
        elif case == "normal":
            assert envelope["text"] is None and envelope["structured"]
        if case == "interrupted_after_delta":
            assert envelope["text"].startswith(envelope["partial_text"])
            assert envelope["partial_text"] != envelope["text"]
        else:
            assert envelope["partial_text"] is None
        if phase == "merge" and envelope["structured"] is not None:
            assert envelope["structured"].keys() == SIX_KEYS
        if case != "normal":
            # Failure cases stay on the default B path (test-only, §15).
            assert branch == "B"


def test_student_and_mentor_bodies_are_d0_02_text():
    for key, envelope in responses().items():
        case, role, _, _, kind, trigger, _ = key.split("/")
        if case != "normal" or role == "analysis":
            continue
        if role == "student":
            assert envelope["text"] == STUDENT[kind]
        else:
            should, feedback, reason = MENTOR[(kind, trigger)]
            assert envelope["structured"] == dict(
                should_intervene=should,
                feedback=feedback,
                reason_summary=reason,
            )


@dataclass(frozen=True)
class DemoTextRequest(TextRequest):
    demo_context: object = field(default=None, kw_only=True)
    demo_selection: object = field(default=None, kw_only=True)


@dataclass(frozen=True)
class DemoStructuredRequest(StructuredRequest):
    demo_context: object = field(default=None, kw_only=True)
    demo_selection: object = field(default=None, kw_only=True)


class Classroom:
    """Sessions with real completed turns in one visitor database."""

    def __init__(self, visitors, ctx, manifest):
        self.visitors, self.ctx, self.sessions = visitors, ctx, 0
        self.manifest = manifest

    async def session(self, path, *, ended):
        texts = dict(INPUTS, unsupported=UNSUPPORTED_INPUT)
        self.sessions += 1
        sid, turns, history = self.sessions, [], []
        async with (
            self.ctx.factory() as db,
            self.visitors.write_guard(self.ctx),
        ):
            if sid == 1:
                await db.execute(
                    text(
                        "INSERT INTO scenario(id,title,is_active,status,config_schema_version,config_version,config_json,review_required,review_reasons,created_at) VALUES(1,'fixture',1,'published',1,1,'{}',0,'[]','2026-10-18')"
                    )
                )
            await db.execute(
                text(
                    "INSERT INTO session(id,scenario_id,teacher_id,started_at,ended_at,tutor_intervention_count,tutor_question_count) VALUES(:id,1,2,'2026-10-18',:ended,0,0)"
                ),
                dict(id=sid, ended="2026-10-19" if ended else None),
            )
            for index, step in enumerate(path, 1):
                _, _, kind = select_content(history, texts[step], INPUTS)
                answer = STUDENT[kind]
                run, turn = str(uuid4()), str(uuid4())
                at = f"2026-10-18 00:{sid:02d}:{index * 2:02d}"
                answered = f"2026-10-18 00:{sid:02d}:{index * 2 + 1:02d}"
                await db.execute(
                    text(
                        "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at,finished_at) VALUES(:id,2,:sid,:turn,'student',:id,'hash','hash','demo','demo-classroom-v1','completed','message',:at,:at)"
                    ),
                    dict(id=run, sid=sid, turn=turn, at=answered),
                )
                for role, content, run_id, created in (
                    ("teacher", texts[step], None, at),
                    ("student", answer, run, answered),
                ):
                    await db.execute(
                        text(
                            "INSERT INTO message(session_id,turn_id,turn_index,role,content,generation_run_id,created_at) VALUES(:sid,:turn,:index,:role,:content,:run,:at)"
                        ),
                        dict(
                            sid=sid,
                            turn=turn,
                            index=index,
                            role=role,
                            content=content,
                            run=run_id,
                            at=created,
                        ),
                    )
                history.append(dict(teacher=dict(content=texts[step])))
                turns.append(turn)
            await db.commit()
            messages = (
                await db.execute(
                    text(
                        "SELECT id,role,content FROM message WHERE session_id=:sid ORDER BY turn_index,created_at,id"
                    ),
                    dict(sid=sid),
                )
            ).mappings()
            messages = [dict(m) for m in messages]
        return sid, turns, messages

    def provider(self, sid, *, failures=False):
        return DemoProvider(
            self.manifest,
            visitors=self.visitors,
            mode="demo",
            session_id=sid,
            config_schema_version=1,
            config=DEMO_CONFIG,
            inputs=INPUTS,
            supported_config_sha256=supported_config_sha256,
            db_schema_version=DB_SCHEMA_VERSION,
            migration_set_sha256=migration_set_sha256(),
            allow_failure_cases=failures,
        )

    async def student(self, path, value, chosen, *, case="normal"):
        sid, _, _ = await self.session(path, ended=False)
        request = DemoTextRequest(
            "demo",
            "demo-classroom-v1",
            "student",
            "fixture",
            [dict(role="user", content=value)],
            dict(max_output_tokens=4096),
            str(uuid4()),
            demo_context=self.ctx,
            demo_selection=replace(chosen, fixture_case=case),
        )
        provider = self.provider(sid, failures=case != "normal")
        return [
            e
            async for e in provider.stream_text(
                request, None, {"student_total": 60}
            )
        ]

    async def structured(
        self, sid, chosen, schema, validation, *, case="normal"
    ):
        request = DemoStructuredRequest(
            "demo",
            "demo-classroom-v1",
            chosen.role,
            "fixture",
            [],
            dict(max_output_tokens=4096),
            str(uuid4()),
            schema,
            validation,
            demo_context=self.ctx,
            demo_selection=replace(chosen, fixture_case=case),
        )
        return await self.provider(
            sid, failures=case != "normal"
        ).generate_structured(request, None, {f"{chosen.role}_total": 60})

    def validation(self, messages, **more):
        return dict(
            messages=messages,
            labels=LABELS,
            classification_enabled=True,
            actor_id=self.ctx.user_id,
            **more,
        )


@pytest.fixture(scope="module")
def fixture_copy(tmp_path_factory):
    # Byte-identical copy off slow mounts; the provider re-checks every hash.
    target = tmp_path_factory.mktemp("fixtures") / "fixtures"
    shutil.copytree(FIXTURES, target)
    return target / "manifest.json"


@pytest.fixture
async def classroom(visitors, fixture_copy):
    _, _, ctx = await ready_context(visitors, "teacher")
    return Classroom(visitors, ctx, fixture_copy)


def _path(state, branch, kind):
    """A shortest real history reaching the target turn of this key."""
    first = [] if branch is None else [branch]
    common = ["common"] if state == "after_compare" else []
    if kind in ("A", "B", "C"):
        return first
    if kind == "common":
        return first + common
    return first + common + ["unsupported"]


def _keys(role, case="normal"):
    return [
        (k, e)
        for k, e in responses().items()
        if k.startswith(f"{case}/{role}/")
    ]


async def test_every_student_key_streams_its_d0_02_text(classroom):
    for key, envelope in _keys("student"):
        _, _, state, branch, kind, _, _ = key.split("/")
        branch = None if branch == "none" else branch
        before = dict(
            initial=[], after_first=[branch], after_compare=[branch, "common"]
        )[state]
        texts = dict(INPUTS, unsupported=UNSUPPORTED_INPUT)
        events = await classroom.student(
            before, texts[kind], selection("student", state, branch, kind)
        )
        assert events[-1].type == "completed", key
        assert events[-1].text == envelope["text"] == STUDENT[kind]
        assert "".join(e.text for e in events[:-1]) == envelope["text"]


async def test_every_mentor_key_validates_for_its_completed_target(classroom):
    for key, envelope in _keys("mentor"):
        _, _, state, branch, kind, trigger, _ = key.split("/")
        branch = None if branch == "none" else branch
        sid, turns, _ = await classroom.session(
            _path(state, branch, kind), ended=False
        )
        chosen = selection(
            "mentor",
            state,
            branch,
            kind,
            trigger=trigger,
            target_turn_id=turns[-1],
        )
        result = await classroom.structured(
            sid, chosen, MentorOutput, dict(trigger=trigger)
        )
        assert result.type == "completed", (key, result.error_code)
        assert result.structured == envelope["structured"]


def _evidence(structured):
    pairs = [
        dict(message_id=e["message_id"], quote=e["quote"])
        for finding in structured["misconception_findings"]
        for e in finding["evidence"]
    ]
    for section in ("strengths", "improvements", "dialogue_coaching"):
        pairs += [
            dict(message_id=i["message_id"], quote=i["quote"])
            for i in structured[section]
        ]
    return pairs


async def test_every_analysis_key_binds_and_validates_each_phase(classroom):
    rubric = dict(A="r1", B="r2", C="r1")
    for state, branch in (
        ("initial", None),
        *(("after_first", x) for x in "ABC"),
        *(("after_compare", x) for x in "ABC"),
    ):
        kind = "common" if state == "after_compare" else branch or "unsupported"
        sid, _, messages = await classroom.session(
            _path(state, branch, kind), ended=True
        )
        single = await classroom.structured(
            sid,
            selection("analysis", state, branch, kind, phase="single"),
            UnifiedAnalysisOutput,
            classroom.validation(messages),
        )
        assert single.type == "completed", (state, branch, single.error_code)
        classes = [
            (c["disposition"], c["rubric_id"])
            for c in single.structured["message_classifications"]
        ]
        assert classes == (
            [("non_analyzable", None)]
            if branch is None
            else [("classified", rubric[branch])]
            + ([("classified", "r3")] if state == "after_compare" else [])
        )
        if state == "after_compare":
            assert [
                f["kind"] for f in single.structured["misconception_findings"]
            ] == [
                "maintained",
                "changed",
            ]
        owned = [m["id"] for m in messages]
        chunk = await classroom.structured(
            sid,
            selection("analysis", state, branch, kind, phase="chunk"),
            UnifiedAnalysisOutput,
            classroom.validation(messages, owned_message_ids=owned),
        )
        assert chunk.type == "completed", chunk.error_code
        assert chunk.structured == single.structured
        merge = await classroom.structured(
            sid,
            selection("analysis", state, branch, kind, phase="merge"),
            MergeAnalysisOutput,
            classroom.validation(
                messages, allowed_evidence=_evidence(single.structured)
            ),
        )
        assert merge.type == "completed", merge.error_code
        assert merge.structured == {
            k: v
            for k, v in single.structured.items()
            if k != "message_classifications"
        }


async def test_b_chunks_and_interleaved_unsupported_follow_projection(
    classroom,
):
    sid, _, messages = await classroom.session(["B", "common"], ended=True)
    chosen = selection(
        "analysis", "after_compare", "B", "common", phase="chunk"
    )
    first = await classroom.structured(
        sid,
        chosen,
        UnifiedAnalysisOutput,
        classroom.validation(
            messages[:2], owned_message_ids=[m["id"] for m in messages[:2]]
        ),
    )
    assert first.type == "completed", first.error_code
    assert [
        c["rubric_id"] for c in first.structured["message_classifications"]
    ] == ["r2"]
    common = await classroom.structured(
        sid,
        chosen,
        UnifiedAnalysisOutput,
        classroom.validation(
            messages[1:], owned_message_ids=[m["id"] for m in messages[2:]]
        ),
    )
    assert common.type == "completed", common.error_code
    assert [
        c["rubric_id"] for c in common.structured["message_classifications"]
    ] == ["r3"]
    assert [f["kind"] for f in common.structured["misconception_findings"]] == [
        "changed"
    ]
    sid, _, messages = await classroom.session(
        ["B", "unsupported", "common"], ended=True
    )
    single = await classroom.structured(
        sid,
        replace(chosen, analysis_phase="single"),
        UnifiedAnalysisOutput,
        classroom.validation(messages),
    )
    assert single.type == "completed", single.error_code
    assert [
        (c["disposition"], c["rubric_id"])
        for c in single.structured["message_classifications"]
    ] == [("classified", "r2"), ("non_analyzable", None), ("classified", "r3")]


async def test_failure_envelopes_terminate_as_declared(classroom):
    for key, envelope in responses().items():
        case, role, state, branch, kind, trigger, phase = key.split("/")
        if case == "normal":
            continue
        trigger = None if trigger == "none" else trigger
        if role == "student":
            events = await classroom.student(
                [],
                INPUTS["B"],
                selection("student", state, branch, kind),
                case=case,
            )
            result = events[-1]
            assert "".join(e.text for e in events[:-1]) == (
                envelope["partial_text"] or ""
            )
        elif role == "mentor":
            sid, turns, _ = await classroom.session(["B"], ended=False)
            result = await classroom.structured(
                sid,
                selection(
                    "mentor",
                    state,
                    branch,
                    kind,
                    trigger=trigger,
                    target_turn_id=turns[0],
                ),
                MentorOutput,
                dict(trigger=trigger),
                case=case,
            )
        else:
            sid, _, messages = await classroom.session(
                ["B", "common"], ended=True
            )
            more = {}
            if phase == "chunk":
                more = dict(owned_message_ids=[m["id"] for m in messages])
            if phase == "merge":
                more = dict(allowed_evidence=[])
            validation = classroom.validation(messages, **more)
            result = await classroom.structured(
                sid,
                selection("analysis", state, branch, kind, phase=phase),
                (
                    MergeAnalysisOutput
                    if phase == "merge"
                    else UnifiedAnalysisOutput
                ),
                validation,
                case=case,
            )
        expected = FAILURES[case]
        if case == "analysis_partial":
            # Valid sections survive; the omitted owned classification is reported.
            assert (result.type, result.error_code) == (
                "error",
                "invalid_output",
            )
            assert validation["validated_analysis"]["misconception_findings"]
            continue
        assert (result.type, result.error_code) == expected, key

"""Deterministic local examples; dependencies are supplied by the demo server."""

import asyncio
import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import text

from src.demo.database import DemoError
from src.demo.output_binding import bind_output
from src.demo.visitors import DemoContext
from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.call_policy import CallDeadline
from src.services.invocation_types import (
    CallEvent,
    InvocationError,
    StructuredRequest,
    empty_usage,
)
from src.services.role_output_contracts import MentorOutput
from src.services.structured_output import structured_event

VERSIONS = dict(
    contract_version="d0-03-v1-r3",
    content_version="d0-02-v1",
    fixture_version="demo-fixture-v1",
)
FAILURES = {
    "interrupted_after_delta": ("interrupted", "interrupted"),
    "refused": ("refused", "refused"),
    **{
        code: ("error", code)
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
    },
    "analysis_partial": ("completed", None),
    "analysis_failed": ("error", "invalid_output"),
}


@dataclass(frozen=True, kw_only=True)
class Selection:
    role: str
    content_state: str
    branch: str | None
    input_kind: str
    manifest_sha256: str
    trigger: str | None = None
    target_turn_id: str | None = None
    analysis_phase: str = "none"
    fixture_case: str = "normal"
    contract_version: str = VERSIONS["contract_version"]
    content_version: str = VERSIONS["content_version"]
    fixture_version: str = VERSIONS["fixture_version"]
    scenario_ref: str = "unit-fraction-v1"


def fixture_lookup_key(selection):
    s = selection
    if (
        s.role not in ("student", "mentor", "analysis")
        or s.content_state not in ("initial", "after_first", "after_compare")
        or s.branch not in (None, "A", "B", "C")
        or s.input_kind not in ("A", "B", "C", "common", "unsupported")
        or s.fixture_case not in ("normal", *FAILURES)
        or (
            s.role == "student"
            and (s.trigger is not None or s.analysis_phase != "none")
        )
        or (
            s.role == "mentor"
            and (
                s.trigger not in ("auto", "manual")
                or s.analysis_phase != "none"
                or s.target_turn_id is None
            )
        )
        or (
            s.role == "analysis"
            and (
                s.trigger is not None
                or s.analysis_phase not in ("single", "chunk", "merge")
            )
        )
        or (s.role != "mentor" and s.target_turn_id is not None)
    ):
        raise InvocationError("INVALID_REQUEST")
    if s.target_turn_id is not None:
        try:
            if str(UUID(s.target_turn_id)) != s.target_turn_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise InvocationError("INVALID_REQUEST") from None
    return "/".join(
        (
            s.fixture_case,
            s.role,
            s.content_state,
            s.branch or "none",
            s.input_kind,
            s.trigger or "none",
            s.analysis_phase,
        )
    )


def select_content(completed_turns, current_input, inputs):
    """Only completed supported pairs advance the educational path."""
    state, branch = "initial", None
    for turn in completed_turns:
        kind = _input_kind(state, turn["teacher"]["content"], inputs)
        if kind in ("A", "B", "C"):
            state, branch = "after_first", kind
        elif kind == "common":
            state = "after_compare"
    return state, branch, _input_kind(state, current_input, inputs)


def _input_kind(state, value, inputs):
    supported = (
        ("A", "B", "C")
        if state == "initial"
        else ("common",) if state == "after_first" else ()
    )
    return next(
        (key for key in supported if value.strip() == inputs[key]),
        "unsupported",
    )


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(value)

    return json.loads(
        raw, object_pairs_hook=pairs, parse_constant=reject_constant
    )


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class DemoProvider:
    """Register this server-bound adapter instance after fixture/DB verification.

    supported_config_sha256 is #195's shared helper, injected until it merges.
    inputs is #197's server-owned A/B/C/common text mapping, never client data.
    """

    def __init__(
        self,
        manifest_path,
        *,
        visitors,
        mode,
        session_id,
        config_schema_version,
        config,
        inputs,
        supported_config_sha256,
        db_schema_version,
        migration_set_sha256,
        clock=None,
        allow_failure_cases=False,
    ):
        self.manifest_path = Path(manifest_path)
        self.visitors = visitors
        self.mode = mode
        self.session_id = session_id
        self.config_schema_version = config_schema_version
        self.config = config
        self.inputs = dict(inputs)
        if (
            set(self.inputs) != {"A", "B", "C", "common"}
            or any(
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                for value in self.inputs.values()
            )
            or len(set(self.inputs.values())) != 4
            or type(session_id) is not int
            or session_id < 1
        ):
            raise InvocationError("INVALID_REQUEST")
        self.supported_config_sha256 = supported_config_sha256
        self.db_schema_version = db_schema_version
        self.migration_set_sha256 = migration_set_sha256
        self.clock = clock
        self.allow_failure_cases = allow_failure_cases

    def _fixture(self, selection):
        try:
            if self.manifest_path.is_symlink():
                raise ValueError("manifest symlink")
            raw = self.manifest_path.read_bytes()
            manifest = _json(raw)
            fixed = dict(
                manifest_version=1,
                **VERSIONS,
                db_schema_version=self.db_schema_version,
                migration_set_sha256=self.migration_set_sha256,
                scenario_config_schema_version=1,
                analysis_schema_version=2,
                provider="demo",
                model_id="demo-classroom-v1",
                capability_definition_version="demo-cap-v1",
                role_contract_versions=dict(
                    student="s1-v1", mentor="s3-v1", analysis="s4-v2"
                ),
                scenario_ref="unit-fraction-v1",
                seed_version="demo-seed-v1",
                seed_timestamp="2026-10-18T00:00:00Z",
                lookup_version=1,
                seed_provenance=dict(
                    source="synthetic",
                    generator="src/demo/seed.py",
                    content_version="d0-02-v1",
                ),
            )
            if (
                manifest.keys()
                != fixed.keys()
                | {"supported_config_sha256", "seed_refs", "files", "responses"}
                or any(
                    type(manifest[k]) is not type(v) or manifest[k] != v
                    for k, v in fixed.items()
                )
                or _sha(raw) != selection.manifest_sha256
                or any(getattr(selection, k) != v for k, v in VERSIONS.items())
                or selection.scenario_ref != manifest["scenario_ref"]
                or not re.fullmatch(
                    "[0-9a-f]{64}", manifest["supported_config_sha256"]
                )
                or not re.fullmatch(
                    "[0-9a-f]{64}", manifest["migration_set_sha256"]
                )
            ):
                raise ValueError("manifest tuple")
            refs = manifest["seed_refs"]
            if refs.keys() != {
                "admin",
                "teacher",
                "group",
                "scenario",
                "completed_session",
                "model",
                "connection",
            }:
                raise ValueError("seed refs")
            for value in refs.values():
                if type(value) is int and value > 0:
                    continue
                if not isinstance(value, str) or str(UUID(value)) != value:
                    raise ValueError("seed ref type")
            paths = []
            file_bytes = {}
            for item in manifest["files"]:
                if item.keys() != {"path", "sha256", "kind"} or item[
                    "kind"
                ] not in ("student", "mentor", "analysis", "failure", "seed"):
                    raise ValueError("file fields")
                relative = PurePosixPath(item["path"])
                path = self.manifest_path.parent / relative
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or str(relative) != item["path"]
                    or "\\" in item["path"]
                    or relative == PurePosixPath(".")
                    or path.resolve() == self.manifest_path.resolve()
                ):
                    raise ValueError("file path")
                for parent in (path, *path.parents):
                    if parent.is_symlink():
                        raise ValueError("symlink")
                    if parent == self.manifest_path.parent:
                        break
                content = path.read_bytes()
                if _sha(content) != item["sha256"]:
                    raise ValueError("file hash")
                paths.append(item["path"])
                file_bytes[item["path"]] = content
            if (
                paths != sorted(set(paths))
                or not isinstance(manifest["responses"], dict)
                or any(
                    not isinstance(k, str) or v not in file_bytes
                    for k, v in manifest["responses"].items()
                )
            ):
                raise ValueError("lookup registration")
            key = fixture_lookup_key(selection)
            envelope = _json(file_bytes[manifest["responses"][key]])
            dimensions = (
                "role",
                "content_state",
                "branch",
                "input_kind",
                "trigger",
                "analysis_phase",
            )
            if (
                envelope.keys()
                != {
                    "fixture_version",
                    "case_id",
                    *dimensions,
                    "terminal",
                    "error_code",
                    "text",
                    "structured",
                    "partial_text",
                }
                or envelope["fixture_version"] != selection.fixture_version
                or envelope["case_id"] != key
                or any(envelope[k] != getattr(selection, k) for k in dimensions)
            ):
                raise ValueError("envelope dimensions")
            case = selection.fixture_case
            if (envelope["terminal"], envelope["error_code"]) != FAILURES.get(
                case, ("completed", None)
            ):
                raise ValueError("terminal case")
            for name in ("text", "partial_text"):
                if envelope[name] is not None and not isinstance(
                    envelope[name], str
                ):
                    raise ValueError("text type")
            if envelope["structured"] is not None and not isinstance(
                envelope["structured"], dict
            ):
                raise ValueError("structured type")
            if case == "normal" and (
                (
                    selection.role == "student"
                    and (
                        not envelope["text"]
                        or envelope["structured"] is not None
                    )
                )
                or (
                    selection.role != "student"
                    and (
                        envelope["text"] is not None
                        or envelope["structured"] is None
                    )
                )
            ):
                raise ValueError("normal body")
            if case == "interrupted_after_delta":
                if (
                    not envelope["partial_text"]
                    or not envelope["text"]
                    or not envelope["text"].startswith(envelope["partial_text"])
                ):
                    raise ValueError("partial prefix")
            elif envelope["partial_text"] is not None:
                raise ValueError("unexpected partial")
            if (
                self.supported_config_sha256(
                    self.config_schema_version, self.config
                )
                != manifest["supported_config_sha256"]
            ):
                raise InvocationError("DEMO_UNSUPPORTED_CONFIGURATION")
            return manifest, envelope
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise InvocationError("VERSION_MISMATCH") from None

    async def _history(self, context):
        async with context.factory() as db:
            actor = (
                await db.execute(
                    text('SELECT role FROM "user" WHERE id=:id'),
                    dict(id=context.user_id),
                )
            ).scalar_one_or_none()
            session = (
                (
                    await db.execute(
                        text(
                            "SELECT teacher_id, ended_at, deleted_at FROM session WHERE id=:sid"
                        ),
                        dict(sid=self.session_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                actor != context.role
                or actor not in ("teacher", "admin")
                or session is None
                or session["deleted_at"] is not None
                or (
                    actor == "teacher"
                    and session["teacher_id"] != context.user_id
                )
            ):
                raise InvocationError("invalid_reference")
            messages = (
                (
                    await db.execute(
                        text(
                            "SELECT m.id, m.role, m.content, m.turn_id, m.turn_index, m.created_at, m.generation_run_id FROM message AS m WHERE m.session_id=:sid ORDER BY m.turn_index, m.created_at, m.id"
                        ),
                        dict(sid=self.session_id),
                    )
                )
                .mappings()
                .all()
            )
            runs = (
                (
                    await db.execute(
                        text(
                            "SELECT id, turn_id, owner_id, finished_at FROM generation_run WHERE session_id=:sid AND operation='student' AND status='completed' AND result_kind='message'"
                        ),
                        dict(sid=self.session_id),
                    )
                )
                .mappings()
                .all()
            )
        completed = []
        for run in runs:
            teachers = [
                m
                for m in messages
                if m["turn_id"] == run["turn_id"] and m["role"] == "teacher"
            ]
            students = [
                m
                for m in messages
                if m["turn_id"] == run["turn_id"]
                and m["role"] == "student"
                and m["generation_run_id"] == run["id"]
            ]
            if (
                len(teachers) != 1
                or len(students) != 1
                or run["owner_id"] != session["teacher_id"]
                or run["finished_at"] is None
                or not run["turn_id"]
                or type(teachers[0]["turn_index"]) is not int
                or teachers[0]["turn_index"] < 1
                or teachers[0]["generation_run_id"] is not None
                or (teachers[0]["created_at"], teachers[0]["id"])
                >= (students[0]["created_at"], students[0]["id"])
                or teachers[0]["turn_index"] != students[0]["turn_index"]
            ):
                raise InvocationError("invalid_reference")
            completed.append(
                dict(
                    turn_id=run["turn_id"],
                    teacher=dict(teachers[0]),
                    student=dict(students[0]),
                )
            )
        completed.sort(
            key=lambda pair: (
                pair["teacher"]["turn_index"],
                pair["teacher"]["created_at"],
                pair["teacher"]["id"],
            )
        )
        for previous, current in zip(completed, completed[1:]):
            if previous["teacher"]["turn_index"] >= current["teacher"][
                "turn_index"
            ] or (
                previous["student"]["created_at"],
                previous["student"]["id"],
            ) >= (
                current["teacher"]["created_at"],
                current["teacher"]["id"],
            ):
                raise InvocationError("invalid_reference")
        return session, completed

    async def _prepare(self, request, secret):
        context = getattr(request, "demo_context", None)
        selection = getattr(request, "demo_selection", None)
        if (
            self.mode != "demo"
            or not isinstance(context, DemoContext)
            or context.app_mode != "demo"
            or context.lease.released
        ):
            raise InvocationError("INVALID_REQUEST")
        await self.visitors.check(context)
        if (
            not isinstance(selection, Selection)
            or request.provider != "demo"
            or request.model_id != "demo-classroom-v1"
            or secret is not None
            or request.service_tier is not None
            or request.role != selection.role
        ):
            raise InvocationError("INVALID_REQUEST")
        options = request.validated_options
        if (
            not isinstance(options, dict)
            or options.keys() != {"max_output_tokens"}
            or type(options["max_output_tokens"]) is not int
            or not 4096 <= options["max_output_tokens"] <= 8192
        ):
            raise InvocationError("INVALID_REQUEST")
        fixture_lookup_key(selection)
        if selection.fixture_case != "normal" and not self.allow_failure_cases:
            raise InvocationError("INVALID_REQUEST")
        manifest, envelope = self._fixture(selection)
        session, history = await self._history(context)
        if selection.role == "student":
            if (
                session["ended_at"] is not None
                or not request.messages
                or request.messages[-1].get("role") != "user"
                or not isinstance(request.messages[-1].get("content"), str)
            ):
                raise InvocationError("INVALID_REQUEST")
            state, branch, kind = select_content(
                history, request.messages[-1]["content"], self.inputs
            )
            expected = (
                state,
                kind if kind in ("A", "B", "C") else branch,
                kind,
            )
        elif selection.role == "analysis":
            if session["ended_at"] is None:
                raise InvocationError("invalid_reference")
            state, branch, _ = select_content(history, "", self.inputs)
            expected = (
                state,
                branch,
                (
                    "common"
                    if state == "after_compare"
                    else branch or "unsupported"
                ),
            )
        else:
            target = next(
                (
                    i
                    for i, turn in enumerate(history)
                    if turn["turn_id"] == selection.target_turn_id
                ),
                None,
            )
            if target is None:
                raise InvocationError("invalid_reference")
            state, branch, kind = select_content(
                history[:target],
                history[target]["teacher"]["content"],
                self.inputs,
            )
            if kind in ("A", "B", "C"):
                state, branch = "after_first", kind
            elif kind == "common":
                state = "after_compare"
            expected = (state, branch, kind)
        if (
            selection.content_state,
            selection.branch,
            selection.input_kind,
        ) != expected:
            raise InvocationError("VERSION_MISMATCH")
        await self.visitors.check(context)
        return envelope, manifest

    def provenance(self, manifest):
        return dict(
            mode="demo",
            response_source="prepared_example",
            external_ai_called=False,
            **VERSIONS,
            record_origin=(
                "seed_prepared"
                if self.session_id == manifest["seed_refs"]["completed_session"]
                else "visitor_generated"
            ),
        )

    def _timeout(self, deadline, first=False):
        now = (
            self.clock()
            if self.clock is not None
            else asyncio.get_running_loop().time()
        )
        if now >= deadline.ends:
            raise InvocationError("timeout_total")
        if (
            first
            and deadline.first_ends is not None
            and now >= deadline.first_ends
        ):
            raise InvocationError("timeout_first_output")

    async def stream_text(self, request, secret, timeouts, *, deadline=None):
        received = False
        owned = deadline is None
        try:
            if request.role != "student":
                raise InvocationError("INVALID_REQUEST")
            deadline = deadline or CallDeadline(timeouts, request.role)
            self._timeout(deadline, first=True)
            async with (
                asyncio.timeout_at(deadline.ends) if owned else nullcontext()
            ):
                envelope, _ = await self._prepare(request, secret)
            self._timeout(deadline, first=True)
            body = (
                envelope["partial_text"]
                if envelope["terminal"] == "interrupted"
                else (
                    envelope["text"]
                    if envelope["terminal"] == "completed"
                    else ""
                )
            )
            for offset in range(0, len(body or ""), 32):
                self._timeout(deadline, first=not received)
                async with (
                    asyncio.timeout_at(deadline.ends)
                    if owned
                    else nullcontext()
                ):
                    await asyncio.sleep(0)
                    self._timeout(deadline, first=not received)
                    await self.visitors.check(request.demo_context)
                self._timeout(deadline, first=not received)
                received = True
                yield CallEvent(
                    "text_delta",
                    text=body[offset : offset + 32],
                    response_received=True,
                )
            self._timeout(deadline, first=not received)
            async with (
                asyncio.timeout_at(deadline.ends) if owned else nullcontext()
            ):
                await self.visitors.check(request.demo_context)
            self._timeout(deadline, first=not received)
            yield CallEvent(
                envelope["terminal"],
                text=(
                    envelope["text"]
                    if envelope["terminal"] == "completed"
                    else ""
                ),
                usage=empty_usage(),
                error_code=envelope["error_code"],
                response_received=received
                or envelope["terminal"] in ("completed", "refused"),
            )
        except asyncio.CancelledError:
            now = (
                self.clock()
                if self.clock is not None
                else asyncio.get_running_loop().time()
            )
            expired = deadline is not None and now >= deadline.ends
            yield CallEvent(
                "error" if expired else "interrupted",
                usage=empty_usage(),
                error_code="timeout_total" if expired else "interrupted",
                response_received=received,
            )
        except TimeoutError:
            yield CallEvent(
                "error",
                usage=empty_usage(),
                error_code="timeout_total",
                response_received=received,
            )
        except (InvocationError, DemoError) as error:
            yield CallEvent(
                "error",
                usage=empty_usage(),
                error_code=error.code,
                response_received=received,
            )

    async def generate_text(self, request, secret, timeouts, *, deadline=None):
        async for event in self.stream_text(
            request, secret, timeouts, deadline=deadline
        ):
            if event.type != "text_delta":
                return event

    async def generate_structured(
        self, request, secret, timeouts, *, deadline=None
    ):
        received = False
        try:
            if not isinstance(request, StructuredRequest):
                raise InvocationError("INVALID_REQUEST")
            envelope, _ = await self._prepare(request, secret)
            selection = request.demo_selection
            schema = (
                MentorOutput
                if selection.role == "mentor"
                else (
                    MergeAnalysisOutput
                    if selection.analysis_phase == "merge"
                    else UnifiedAnalysisOutput
                )
            )
            if (
                request.role not in ("mentor", "analysis")
                or request.output_schema is not schema
            ):
                raise InvocationError("VERSION_MISMATCH")
            deadline = deadline or CallDeadline(timeouts, request.role)
            self._timeout(deadline, first=True)
            if envelope["terminal"] != "completed":
                return CallEvent(
                    envelope["terminal"],
                    usage=empty_usage(),
                    error_code=envelope["error_code"],
                    response_received=envelope["terminal"] == "refused",
                )
            received = True
            bound = await bind_output(
                envelope["structured"],
                context=request.demo_context,
                visitors=self.visitors,
                session_id=self.session_id,
                selection=selection,
                validation_context=request.validation_context,
                inputs=self.inputs,
            )
            if request.role == "mentor":
                expected = (
                    selection.trigger == "manual"
                    or selection.input_kind in ("A", "B", "C")
                )
                if (
                    bound.get("should_intervene") is not expected
                    or request.validation_context.get(
                        "trigger", selection.trigger
                    )
                    != selection.trigger
                ):
                    raise InvocationError("invalid_output")
            await asyncio.sleep(0)
            await self.visitors.check(request.demo_context)
            self._timeout(deadline, first=True)
            return structured_event(
                request,
                CallEvent(
                    "completed",
                    text=json.dumps(bound, ensure_ascii=False, allow_nan=False),
                    usage=empty_usage(),
                    response_received=True,
                ),
            )
        except asyncio.CancelledError:
            now = (
                self.clock()
                if self.clock is not None
                else asyncio.get_running_loop().time()
            )
            expired = deadline is not None and now >= deadline.ends
            return CallEvent(
                "error" if expired else "interrupted",
                usage=empty_usage(),
                error_code="timeout_total" if expired else "interrupted",
                response_received=received,
            )
        except (InvocationError, DemoError) as error:
            return CallEvent(
                "error",
                usage=empty_usage(),
                error_code=error.code,
                response_received=received,
            )

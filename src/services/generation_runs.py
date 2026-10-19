"""Short SQLite transactions reserve and finalize student generation rights."""

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text

from src.api.routes.session_helpers import (
    load_session,
    require_native_session,
    validate_scenario_access,
)
from src.config import config
from src.demo.database import DemoError
from src.demo.provider import DemoProvider, Selection, select_content
from src.i18n import translate
from src.models import (
    AppSetting,
    GenerationRun,
    Message,
    Scenario,
    Session,
)
from src.models.scenario_group import ScenarioGroup
from src.services.call_admission import approve_call, execution_lock
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.lesson_snapshots import read_lesson_snapshot
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.services.student_bot import build_student_request
from src.services.turn_context import load_completed_turns


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def conflict(code, **extra):
    raise HTTPException(409, detail={"code": code, **extra})


@dataclass(frozen=True)
class DemoBinding:
    """Server-owned demo call inputs, built by the route from app state.

    ctx is the run's DemoContext holding its registry lease; inputs is
    #197's A/B/C/common mapping and supported_config_sha256 #195's helper.
    """

    ctx: object
    registry: object
    seed: object
    inputs: dict
    supported_config_sha256: object

    def guard(self):
        return self.registry.write_guard(self.ctx)

    def error(self, code):
        return DemoError(
            code, generation=self.ctx.generation, auth_epoch=self.ctx.auth_epoch
        )

    def supported(self, session):
        """§16: refuse an unsupported snapshot before any turn or call."""
        raw = self.seed.manifest_path.read_bytes()
        envelope = session.config_snapshot_json
        try:
            supported = (
                self.supported_config_sha256(
                    envelope["schema_version"], envelope["config"]
                )
                == json.loads(raw)["supported_config_sha256"]
            )
        except (KeyError, TypeError, ValueError):
            supported = False
        if not supported:
            raise self.error("UNSUPPORTED_CONFIGURATION")
        return hashlib.sha256(raw).hexdigest()

    def selection(self, manifest_sha256, history, current, *, role, **fields):
        """The server's own derivation; the adapter re-derives and compares."""
        state, branch, kind = select_content(
            [{"teacher": {"content": m["content"]}} for m in history[0::2]],
            current,
            self.inputs,
        )
        if kind in ("A", "B", "C"):
            branch = kind
            if role == "mentor":
                state = "after_first"
        elif kind == "common" and role == "mentor":
            state = "after_compare"
        return Selection(
            role=role,
            content_state=state,
            branch=branch,
            input_kind=kind,
            manifest_sha256=manifest_sha256,
            **fields,
        )

    def bind(self, request, selection):
        return replace(request, demo_context=self.ctx, demo_selection=selection)

    def adapter(self, session):
        envelope = session.config_snapshot_json
        return DemoProvider(
            self.seed.manifest_path,
            visitors=self.registry.visitors,
            mode="demo",
            session_id=session.id,
            config_schema_version=envelope["schema_version"],
            config=envelope["config"],
            inputs=self.inputs,
            supported_config_sha256=self.supported_config_sha256,
            db_schema_version=self.seed.db_schema_version,
            migration_set_sha256=self.seed.migration_set_sha256,
        )


async def snapshot(db, run):
    if run.operation == "analysis":
        from src.services.analysis_runs import run_response

        return await run_response(db, run)
    teacher = (
        await db.scalars(
            select(Message).where(
                Message.session_id == run.session_id,
                Message.turn_id == run.turn_id,
                Message.role == "teacher",
            )
        )
    ).one()
    message = (
        await db.scalars(
            select(Message).where(Message.generation_run_id == run.id)
        )
    ).one_or_none()
    return {
        "run_id": run.id,
        "request_id": run.request_id,
        "session_id": run.session_id,
        "turn_id": run.turn_id,
        "turn_index": teacher.turn_index,
        "operation": run.operation,
        "status": run.status,
        "teacher_message_id": teacher.id,
        "result_kind": run.result_kind,
        "message": (
            None
            if message is None
            else {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            }
        ),
        "partial_text": run.partial_text,
        "error_code": run.error_code,
        "retryable": run.status in {"failed", "interrupted"},
    }


async def reserve_student(
    factory, session_id, user, request, *, language="ko", demo=None, run_id=None
):
    async with execution_lock():
        # Lock order: admission, then the visitor transition, then the DB.
        async with demo.guard() if demo else nullcontext():
            return await _reserve_student(
                factory,
                session_id,
                user,
                request,
                language=language,
                demo=demo,
                run_id=run_id,
            )


async def _reserve_student(
    factory, session_id, user, request, *, language="ko", demo=None, run_id=None
):
    input_hash = digest(
        {
            "owner": user.id,
            "session": session_id,
            "operation": "student",
            "turn_id": request.turn_id,
            "content": request.content,
        }
    )
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        session = await load_session(session_id, user, db, language=language)
        scenario = await db.get(Scenario, session.scenario_id)
        if not user.is_admin:
            access = await db.scalar(
                select(ScenarioGroup.id).where(
                    ScenarioGroup.scenario_id == scenario.id,
                    ScenarioGroup.group_id == user.group_id,
                )
            )
            if access is None:
                raise HTTPException(
                    403, detail=translate("teacher.forbidden", language)
                )
        require_native_session(session, language=language)
        existing = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.owner_id == user.id,
                GenerationRun.session_id == session_id,
                GenerationRun.request_id == request.request_id,
            )
        )
        if existing:
            if existing.input_hash != input_hash:
                if demo:
                    raise demo.error("REQUEST_CONFLICT")
                conflict("request_conflict")
            return await snapshot(db, existing), None
        if request.turn_id:
            teacher = await db.scalar(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.turn_id == request.turn_id,
                    Message.role == "teacher",
                )
            )
            if teacher is None:
                raise HTTPException(
                    404, detail=translate("teacher.turn_not_found", language)
                )
            if teacher.content != request.content:
                if demo:
                    raise demo.error("REQUEST_CONFLICT")
                conflict("request_conflict")
            completed = await db.scalar(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id,
                    GenerationRun.turn_id == teacher.turn_id,
                    GenerationRun.operation == "student",
                    GenerationRun.status == "completed",
                )
            )
            if completed:
                return await snapshot(db, completed), None
        else:
            teacher = None
        if session.ended_at:
            raise HTTPException(
                400,
                detail={
                    "code": "session_ended",
                    "message": translate(
                        "teacher.conversation_ended", language
                    ),
                },
            )
        await validate_scenario_access(scenario.id, user, db, language=language)
        if not scenario.is_active:
            raise HTTPException(
                404, detail=translate("teacher.scenario_not_found", language)
            )
        busy = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "student",
                GenerationRun.status == "running",
            )
        )
        if busy:
            conflict("student_busy", run_id=busy.id)
        manifest_sha256 = demo.supported(session) if demo else None
        try:
            lesson = read_lesson_snapshot(session, language=language)
            connection, model, options = await resolve_frozen_model(
                db,
                lesson.config.student.resolved_model_config,
                "student",
                app_mode="demo" if demo else config.APP_MODE,
            )
        except (InvocationError, HTTPException):
            raise HTTPException(
                503,
                detail={
                    "code": "configuration_unavailable",
                    "message": translate(
                        "teacher.student_configuration_error", language
                    ),
                },
            ) from None
        if teacher is None:
            answered = select(GenerationRun.turn_id).where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "student",
                GenerationRun.status == "completed",
            )
            unresolved = await db.scalar(
                select(Message.id).where(
                    Message.session_id == session_id,
                    Message.role == "teacher",
                    Message.turn_id.is_not(None),
                    Message.turn_id.not_in(answered),
                )
            )
            if unresolved:
                conflict("unresolved_turn")
            index = await db.scalar(
                select(func.max(Message.turn_index)).where(
                    Message.session_id == session_id, Message.role == "teacher"
                )
            )
            teacher = Message(
                session_id=session_id,
                role="teacher",
                content=request.content,
                turn_id=str(uuid4()),
                turn_index=(index or 0) + 1,
            )
            db.add(teacher)
            await db.flush()
        history = await load_completed_turns(
            db,
            session_id,
            before_turn_index=teacher.turn_index,
        )
        try:
            call = build_student_request(
                lesson,
                request.content,
                history,
                connection,
                model,
                options,
                request.request_id,
            )
        except InvocationError as error:
            raise HTTPException(
                422 if error.code == "context_limit" else 503,
                detail={
                    "code": error.code,
                    "message": translate("teacher.shorten_question", language),
                },
            ) from None
        if demo:
            call = demo.bind(
                call,
                demo.selection(
                    manifest_sha256, history, request.content, role="student"
                ),
            )
        run = GenerationRun(
            id=run_id or str(uuid4()),
            owner_id=user.id,
            session_id=session_id,
            turn_id=teacher.turn_id,
            operation="student",
            request_id=request.request_id,
            input_hash=input_hash,
            config_hash=session.config_hash,
            provider=connection.provider,
            model=model.model_id,
            status="running",
        )
        db.add(run)
        await db.flush()
        accepted = await snapshot(db, run)
        setting = await db.get(AppSetting, 1)
        if setting is None:
            raise HTTPException(
                503, detail={"code": "configuration_unavailable"}
            )
        try:
            permit = approve_call(
                factory,
                connection,
                setting,
                owner_id=user.id,
                operation="student",
                role="student",
                admin=False,
                ctx=demo and demo.ctx,
                registry=demo and demo.registry,
            )
        except InvocationError as error:
            raise HTTPException(
                429 if error.code == "call_limit_reached" else 503,
                detail={
                    "code": error.code,
                    "message": translate("teacher.check_ai_settings", language),
                },
            ) from None
        permit.model_options = options
        permit.model_config_id = model.id
        permit.config_version = model.config_version
        permit.model_id = model.model_id
        permit.capability_version = model.capability_definition_version
        permit.contract_version = ROLE_CONTRACT_VERSIONS["student"]
        try:
            await db.commit()
        except BaseException:
            permit.release()
            raise
        execution = {"request": call, "permit": permit}
        if demo:
            execution["demo_adapter"] = demo.adapter(session)
        return accepted, execution


async def finish_student(
    factory,
    run_id,
    *,
    status,
    content=None,
    partial_text=None,
    error_code=None,
    first_output_at=None,
    demo=None,
):
    # Late saves stay on the run's own factory and fail its guard when stale.
    async with demo.guard() if demo else nullcontext(), factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id)
        if run.status != "running":
            return await snapshot(db, run)
        session = await db.get(Session, run.session_id)
        if session.ended_at or session.deleted_at:
            status, error_code = "cancelled", "session_ended"
        run.status = status
        run.error_code = error_code
        run.first_output_at = first_output_at
        run.finished_at = datetime.now(timezone.utc)
        run.partial_text = None if status == "completed" else partial_text
        if status == "completed":
            teacher = await db.scalar(
                select(Message).where(
                    Message.session_id == run.session_id,
                    Message.turn_id == run.turn_id,
                    Message.role == "teacher",
                )
            )
            db.add(
                Message(
                    session_id=run.session_id,
                    role="student",
                    content=content,
                    turn_id=run.turn_id,
                    turn_index=teacher.turn_index,
                    generation_run_id=run.id,
                )
            )
            run.result_kind = "message"
        await db.flush()
        result = await snapshot(db, run)
        try:
            await db.commit()
        except BaseException:
            # Discard a connection whose SQLite commit state is uncertain.
            await db.invalidate()
            raise
        return result

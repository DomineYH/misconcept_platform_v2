"""Reserve mentor-only execution rights and commit coaching with its counter."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import aliased

from src.api.routes.session_helpers import (
    load_session,
    require_native_session,
    validate_scenario_access,
)
from src.models import (
    AppSetting,
    GenerationRun,
    Message,
    Scenario,
    Session,
)
from src.models.scenario_group import ScenarioGroup
from src.services.call_admission import approve_call, execution_lock
from src.services.generation_runs import conflict, digest, snapshot
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.lesson_snapshots import read_lesson_snapshot
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.services.turn_context import load_mentor_context
from src.services.tutor_bot import build_mentor_request


async def reserve_mentor(
    factory, session_id, turn_id, user, request_id, trigger
):
    async with execution_lock():
        try:
            return await _reserve_mentor(
                factory, session_id, turn_id, user, request_id, trigger
            )
        except InvocationError as error:
            raise HTTPException(
                (
                    422
                    if error.code == "context_limit"
                    else 429 if error.code == "call_limit_reached" else 503
                ),
                detail={
                    "code": error.code,
                    "message": (
                        "질문을 짧게 수정하거나 관리자에게 AI 설정을 확인해주세요."
                        if error.code == "context_limit"
                        else "관리자에게 AI 연결과 멘토 모델 검증을 요청하거나 잠시 후 다시 시도해주세요."
                    ),
                },
                headers=(
                    {"Retry-After": "1"}
                    if error.code == "call_limit_reached"
                    else None
                ),
            ) from None


async def _reserve_mentor(
    factory, session_id, turn_id, user, request_id, trigger
):
    input_hash = digest(
        {
            "owner": user.id,
            "session": session_id,
            "operation": "mentor",
            "turn_id": turn_id,
            "trigger": trigger,
        }
    )
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        session = await load_session(session_id, user, db)
        scenario = await db.get(Scenario, session.scenario_id)
        if not user.is_admin:
            access = await db.scalar(
                select(ScenarioGroup.id).where(
                    ScenarioGroup.scenario_id == scenario.id,
                    ScenarioGroup.group_id == user.group_id,
                )
            )
            if access is None:
                raise HTTPException(403, detail="Forbidden")
        require_native_session(session)
        existing = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.owner_id == user.id,
                GenerationRun.session_id == session_id,
                GenerationRun.request_id == request_id,
            )
        )
        if existing:
            if existing.input_hash != input_hash:
                conflict("request_conflict")
            return await snapshot(db, existing), None
        teacher = await db.scalar(
            select(Message).where(
                Message.session_id == session_id,
                Message.turn_id == turn_id,
                Message.role == "teacher",
            )
        )
        if teacher is None:
            raise HTTPException(404, detail="Turn not found")
        completed = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.session_id == session_id,
                GenerationRun.turn_id == turn_id,
                GenerationRun.operation == "mentor",
                GenerationRun.status == "completed",
                GenerationRun.result_kind == "message",
            )
        )
        if completed:
            return await snapshot(db, completed), None
        if session.ended_at:
            raise HTTPException(400, detail={"code": "session_ended"})
        await validate_scenario_access(scenario.id, user, db)
        if not scenario.is_active:
            raise HTTPException(404, detail="Scenario not found")
        lesson = read_lesson_snapshot(session)
        if lesson.config.mentor.mode == "off":
            raise HTTPException(400, detail={"code": "mentor_disabled"})
        if trigger == "auto" and lesson.config.mentor.mode != "auto":
            raise HTTPException(400, detail={"code": "mentor_auto_disabled"})
        student = await db.scalar(
            select(Message.id).where(
                Message.session_id == session_id,
                Message.turn_id == turn_id,
                Message.role == "student",
            )
        )
        if student is None:
            conflict("turn_incomplete")
        answers = aliased(Message)
        completed_turns = (
            await db.scalars(
                select(Message.turn_id)
                .join(
                    answers,
                    (answers.session_id == Message.session_id)
                    & (answers.turn_id == Message.turn_id)
                    & (answers.role == "student"),
                )
                .where(
                    Message.session_id == session_id,
                    Message.role == "teacher",
                    Message.turn_id.is_not(None),
                    Message.turn_index.is_not(None),
                )
                .order_by(Message.turn_index)
            )
        ).all()
        if not completed_turns or completed_turns[-1] != turn_id:
            conflict("mentor_turn_obsolete")
        positions = {
            target: index for index, target in enumerate(completed_turns, 1)
        }
        runs = (
            await db.scalars(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id,
                    GenerationRun.operation == "mentor",
                )
            )
        ).all()
        if trigger == "auto":
            prior = next(
                (
                    run
                    for run in reversed(runs)
                    if run.turn_id == turn_id and run.mentor_trigger == "auto"
                ),
                None,
            )
            if prior is not None:
                return await snapshot(db, prior), None
            policy = lesson.config.mentor.intervention_policy
            position = positions[turn_id]
            if position < policy.start_turn:
                conflict("mentor_start_turn")
            last_check = max(
                (
                    positions.get(run.turn_id, 0)
                    for run in runs
                    if run.mentor_trigger == "auto"
                ),
                default=0,
            )
            if last_check and position - last_check < policy.min_interval_turns:
                conflict("mentor_interval")
        if lesson.config.mentor.mode == "auto":
            policy = lesson.config.mentor.intervention_policy
            first = max(1, positions[turn_id] - policy.window_turns + 1)
            reserved = sum(
                first <= positions.get(run.turn_id, 0) <= positions[turn_id]
                and (
                    run.status == "running"
                    or (
                        run.status == "completed"
                        and run.result_kind == "message"
                    )
                )
                for run in runs
            )
            if reserved >= policy.max_interventions:
                conflict("mentor_limit")
        busy = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "mentor",
                GenerationRun.status == "running",
            )
        )
        if busy:
            conflict("mentor_busy", run_id=busy.id)
        previous = await db.scalar(
            select(GenerationRun.id)
            .where(
                GenerationRun.session_id == session_id,
                GenerationRun.turn_id == turn_id,
                GenerationRun.operation == "mentor",
            )
            .limit(1)
        )
        history = await load_mentor_context(
            db,
            session_id,
            turn_id,
            limit=lesson.config.runtime.context_turn_limit,
        )
        connection, model, options = await resolve_frozen_model(
            db, lesson.config.mentor.resolved_model_config, "mentor"
        )
        request = build_mentor_request(
            lesson, history, trigger, options, request_id
        )
        run = GenerationRun(
            id=str(uuid4()),
            owner_id=user.id,
            session_id=session_id,
            turn_id=turn_id,
            operation="mentor",
            mentor_trigger=trigger,
            request_id=request_id,
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
            raise InvocationError("configuration_unavailable")
        permit = approve_call(
            factory,
            connection,
            setting,
            owner_id=user.id,
            operation="mentor",
            role="mentor",
            admin=False,
        )
        permit.model_config_id = model.id
        permit.config_version = model.config_version
        permit.model_id = model.model_id
        permit.capability_version = model.capability_definition_version
        permit.contract_version = ROLE_CONTRACT_VERSIONS["mentor"]
        permit.model_options = options
        if previous is None:
            session.tutor_question_count += 1
        execution = {
            "request": request,
            "permit": permit,
            "deadline": permit.admitted_at + permit.timeouts["mentor_total"],
        }
        try:
            await db.commit()
        except BaseException:
            if permit is not None:
                permit.release()
            raise
        return accepted, execution


async def finish_mentor(
    factory,
    run_id,
    *,
    status,
    content=None,
    reason_summary=None,
    error_code=None,
):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id)
        if run.status != "running":
            return await snapshot(db, run)
        session = await db.get(Session, run.session_id)
        if session.ended_at or session.deleted_at:
            status, error_code = "cancelled", "session_ended"
        run.status = status
        run.error_code = error_code
        run.finished_at = datetime.now(timezone.utc)
        if status == "completed":
            run.mentor_reason_summary = reason_summary
            run.result_kind = "message" if content else "no_intervention"
            if content:
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
                        role="tutor",
                        content=content,
                        turn_id=run.turn_id,
                        turn_index=teacher.turn_index,
                        generation_run_id=run.id,
                    )
                )
                session.tutor_intervention_count += 1
                run.first_output_at = run.finished_at
        await db.flush()
        result = await snapshot(db, run)
        try:
            await db.commit()
        except BaseException:
            await db.invalidate()
            raise
        return result

"""Short SQLite transactions reserve and finalize student generation rights."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text

from src.api.routes.session_helpers import (
    load_session,
    validate_public_problem,
    validate_scenario_access,
)
from src.config import config
from src.models import (
    ApiUsageLog,
    GenerationRun,
    Message,
    Scenario,
    Session,
    calculate_cost,
)
from src.models.scenario_group import ScenarioGroup
from src.services.prompt_manager import PromptManager
from src.services.student_bot import build_student_input


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def conflict(code, **extra):
    raise HTTPException(409, detail={"code": code, **extra})


async def snapshot(db, run):
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


async def reserve_student(factory, session_id, user, request):
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
        existing = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.owner_id == user.id,
                GenerationRun.session_id == session_id,
                GenerationRun.request_id == request.request_id,
            )
        )
        if existing:
            if existing.input_hash != input_hash:
                conflict("request_conflict")
            return await snapshot(db, existing), None
        if session.ended_at:
            raise HTTPException(400, detail="Session already ended")
        await validate_scenario_access(scenario.id, user, db)
        validate_public_problem(scenario)
        if request.turn_id:
            teacher = await db.scalar(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.turn_id == request.turn_id,
                    Message.role == "teacher",
                )
            )
            if teacher is None:
                raise HTTPException(404, detail="Turn not found")
            if teacher.content != request.content:
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
        busy = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "student",
                GenerationRun.status == "running",
            )
        )
        if busy:
            conflict("student_busy", run_id=busy.id)
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
        template = await PromptManager.get_template_text_by_id(
            db, scenario.student_template_id
        )
        # ponytail: retain message-count context until #30 adds completed pairs.
        history = (
            await db.scalars(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    Message.role.in_(["teacher", "student"]),
                    Message.id < teacher.id,
                )
                .order_by(Message.id.desc())
                .limit(config.CONTEXT_WINDOW_TURNS)
            )
        ).all()
        kwargs = {
            "model": scenario.chat_model or config.CHAT_MODEL,
            "reasoning": {"effort": config.STUDENT_REASONING},
            "max_output_tokens": config.STUDENT_MAX_TOKENS,
            "input": build_student_input(
                template,
                scenario.prompt,
                scenario.title,
                scenario.student_profile or "Grade 5 student",
                request.content,
                [
                    {"role": m.role, "content": m.content}
                    for m in reversed(history)
                ],
            ),
        }
        run = GenerationRun(
            id=str(uuid4()),
            owner_id=user.id,
            session_id=session_id,
            turn_id=teacher.turn_id,
            operation="student",
            request_id=request.request_id,
            input_hash=input_hash,
            config_hash=digest(
                {
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key != "input"
                    },
                    "instructions": kwargs["input"][:2],
                }
            ),
            provider="openai",
            model=kwargs["model"],
            status="running",
        )
        db.add(run)
        await db.flush()
        accepted = await snapshot(db, run)
        await db.commit()
        return accepted, kwargs


async def finish_student(
    factory,
    run_id,
    *,
    status,
    content=None,
    partial_text=None,
    error_code=None,
    first_output_at=None,
    usage=None,
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
        if usage:
            db.add(
                ApiUsageLog(
                    session_id=run.session_id,
                    bot_type="student",
                    model=run.model,
                    **usage,
                    estimated_cost_usd=calculate_cost(
                        run.model,
                        usage["prompt_tokens"],
                        usage["completion_tokens"],
                    ),
                )
            )
        await db.flush()
        result = await snapshot(db, run)
        try:
            await db.commit()
        except BaseException:
            # Discard a connection whose SQLite commit state is uncertain.
            await db.invalidate()
            raise
        return result

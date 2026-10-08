"""Reserve mentor-only execution rights and commit coaching with its counter."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from src.api.routes.session_helpers import (
    load_session,
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
from src.services.generation_runs import conflict, digest, snapshot
from src.services.prompt_manager import PromptManager
from src.services.turn_context import load_mentor_context
from src.services.tutor_bot import advance_question_count


async def reserve_mentor(factory, session_id, turn_id, user, request_id):
    input_hash = digest(
        {
            "owner": user.id,
            "session": session_id,
            "operation": "mentor",
            "turn_id": turn_id,
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
            )
        )
        if completed:
            return await snapshot(db, completed), None
        if session.ended_at:
            raise HTTPException(400, detail={"code": "session_ended"})
        await validate_scenario_access(scenario.id, user, db)
        if scenario.tutor_template_id is None:
            raise HTTPException(400, detail={"code": "mentor_disabled"})
        student = await db.scalar(
            select(Message.id).where(
                Message.session_id == session_id,
                Message.turn_id == turn_id,
                Message.role == "student",
            )
        )
        if student is None:
            conflict("turn_incomplete")
        newer = await db.scalar(
            select(GenerationRun.id)
            .join(
                Message,
                (Message.session_id == GenerationRun.session_id)
                & (Message.turn_id == GenerationRun.turn_id)
                & (Message.role == "teacher"),
            )
            .where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "mentor",
                Message.turn_index > teacher.turn_index,
            )
            .limit(1)
        )
        if newer:
            conflict("mentor_turn_obsolete")
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
        template = await PromptManager.get_template_text_by_id(
            db, scenario.tutor_template_id
        )
        history = await load_mentor_context(db, session_id, turn_id)
        if previous is None:
            (session.tutor_question_count, session.tutor_intervention_count) = (
                advance_question_count(
                    session.tutor_question_count,
                    session.tutor_intervention_count,
                )
            )
        options = {
            "template_id": scenario.tutor_template_id,
            "scenario_title": scenario.title,
            "prompt": scenario.prompt,
            "student_profile": scenario.student_profile or "Grade 5 student",
            "model": config.ANALYSIS_MODEL,
            "reasoning_effort": config.TUTOR_REASONING,
            "max_tokens": config.TUTOR_MAX_TOKENS,
            "intervention_threshold": (
                scenario.tutor_intervention_threshold
                or config.TUTOR_INTERVENTION_THRESHOLD
            ),
            "sensitivity": scenario.tutor_sensitivity,
        }
        run = GenerationRun(
            id=str(uuid4()),
            owner_id=user.id,
            session_id=session_id,
            turn_id=turn_id,
            operation="mentor",
            request_id=request_id,
            input_hash=input_hash,
            config_hash=digest(
                {
                    **options,
                    "template": template,
                    "dialogue_model": config.DIALOGUE_ANALYSIS_MODEL,
                }
            ),
            provider="openai",
            model=options["model"],
            status="running",
        )
        db.add(run)
        await db.flush()
        accepted = await snapshot(db, run)
        execution = {
            "options": {
                **options,
                "initial_question_count": session.tutor_question_count,
                "initial_intervention_count": session.tutor_intervention_count,
            },
            "template": template,
            "history": history,
        }
        await db.commit()
        return accepted, execution


async def finish_mentor(
    factory,
    run_id,
    *,
    status,
    content=None,
    usage=None,
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
        if usage:
            db.add(
                ApiUsageLog(
                    session_id=run.session_id,
                    bot_type="tutor",
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
            await db.invalidate()
            raise
        return result

"""Durable single-call analysis reservations; the database owns execution rights."""

import asyncio
import json
import logging
from datetime import timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models import GenerationRun, Message, Session, User
from src.models.provider_connection import now
from src.services import analysis_pipeline
from src.services.analysis_pipeline import (
    load_analysis_lesson,
)
from src.services.analysis_results import (
    analysis_status,
    load_analysis_response,
    load_summary,
    save_analysis,
)
from src.services.call_admission import execution_lock
from src.services.generation_runs import conflict, digest
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.services.session_synthesizer import prompt_hash
from src.utils.cache import load_prompt_template

logger = logging.getLogger(__name__)
RUN_SECONDS = 900
# shortcut: tasks require one worker, coordinate cancellation before adding workers.
active_analyses: dict[str, asyncio.Task] = {}
cleanup_tasks: set[asyncio.Task] = set()


def run_state(run, report=None):
    outcome = json.loads(run.outcome_json or "{}")
    adopted = run.accepted_report_id is not None
    current = (
        report is not None
        and json.loads(report.payload_json).get("metadata", {}).get("run_id")
        == run.id
    )
    return dict(
        run_id=run.id,
        request_id=run.request_id,
        status=run.status,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        error_code=run.error_code,
        outcome=outcome,
        adopted=adopted,
        superseded=adopted and not current,
        preserved=report is not None and not current,
        accepted_report_id=run.accepted_report_id,
        accepted_report_version=run.accepted_report_version,
    )


async def run_response(db, run, *, admin=False):
    response = await load_analysis_response(run.session_id, db, admin=admin)
    await db.refresh(run)
    _, report = await load_summary(run.session_id, db)
    state = run_state(run, report)
    state["preserved"] = (
        response.get("accepted_report") is not None
        and response["accepted_report"].get("run_id") != run.id
    )
    if response["latest_run"].get("run_id") != run.id:
        response.pop("regeneration_status", None)
    response.update(
        run_id=run.id,
        request_id=run.request_id,
        session_id=run.session_id,
        latest_run=state,
    )
    if state["superseded"]:
        response["accepted_report"] = None
        response.update(
            feedback=None,
            feedback_sections=None,
            distribution={},
            questions=[],
            grade_counts={},
        )
        for message in response["messages"]:
            message.update(label=None, grade=None, level=None)
    prefix = "/admin" if admin else ""
    base = f"{prefix}/sessions/{run.session_id}/analysis/runs/{run.id}"
    response["actions"].update(status=base, cancel=base + "/cancel")
    return response


async def reserve_analysis(
    factory,
    session_id,
    actor_id,
    request_id,
    *,
    regenerate=False,
    plan_hash=None,
):
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            session = await db.get(Session, session_id)
            user = await db.get(User, actor_id)
            if session is None or session.deleted_at is not None:
                raise HTTPException(404, detail="Session not found")
            if user is None or (
                not user.is_admin and session.teacher_id != actor_id
            ):
                raise HTTPException(403, detail="Forbidden")
            from src.api.routes.session_helpers import require_native_session

            require_native_session(session)
            if not session.ended_at:
                raise HTTPException(
                    400, detail="Session must be ended before analysis"
                )
            from src.services.lesson_snapshots import read_lesson_snapshot

            if regenerate and not user.is_admin:
                raise HTTPException(403, detail="Forbidden")
            lesson = read_lesson_snapshot(session)
            messages = list(
                await db.scalars(
                    select(Message)
                    .where(
                        Message.session_id == session_id,
                        Message.role.in_(["teacher", "student"]),
                    )
                    .order_by(Message.created_at, Message.id)
                )
            )
            plan = dict(
                version=1,
                regenerate=regenerate,
                mode="single",
                schema_version=2,
                contract_version=ROLE_CONTRACT_VERSIONS["analysis"],
                prompt_version=prompt_hash(
                    load_prompt_template("analysis_v2.txt")
                ),
                message_ids=[m.id for m in messages],
            )
            fingerprint = digest(
                dict(
                    session_id=session_id,
                    actor_id=actor_id,
                    messages=[
                        dict(id=m.id, role=m.role, content=m.content)
                        for m in messages
                    ],
                    snapshot=lesson.model_dump(),
                    plan=plan,
                    regenerate=regenerate,
                )
            )
            existing = await db.scalar(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id,
                    GenerationRun.owner_id == actor_id,
                    GenerationRun.request_id == request_id,
                )
            )
            if existing:
                if (
                    existing.operation != "analysis"
                    or existing.input_hash != fingerprint
                ):
                    conflict("request_conflict")
                return (
                    await run_response(db, existing, admin=user.is_admin),
                    None,
                )
            if plan_hash is not None and plan_hash != digest(plan):
                conflict("plan_conflict")
            busy = await db.scalar(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id,
                    GenerationRun.operation == "analysis",
                    GenerationRun.status == "running",
                )
            )
            if busy:
                conflict("analysis_busy", run_id=busy.id)
            summary, report = await load_summary(session_id, db)
            if (
                not regenerate
                and summary
                and analysis_status(summary, report) != "failed"
            ):
                return (
                    await load_analysis_response(
                        session_id, db, admin=user.is_admin
                    ),
                    None,
                )
            lesson = await load_analysis_lesson(db, session_id, actor_id)
            connection, model, _ = await resolve_frozen_model(
                db, lesson.config.analysis.resolved_model_config, "analysis"
            )
            run_id = str(uuid4())
            run = GenerationRun(
                id=run_id,
                owner_id=actor_id,
                session_id=session_id,
                turn_id=run_id,
                operation="analysis",
                request_id=request_id,
                input_hash=fingerprint,
                config_hash=digest(lesson.model_dump()),
                provider=connection.provider,
                model=model.model_id,
                status="running",
                plan_json=json.dumps({**plan, "plan_hash": digest(plan)}),
                started_at=now(),
            )
            db.add(run)
            await db.flush()
            response = await run_response(db, run, admin=user.is_admin)
            await db.commit()
            return response, dict(
                run_id=run_id,
                session_id=session_id,
                all_messages=messages,
                teacher_messages=[m for m in messages if m.role == "teacher"],
                snapshot=lesson,
                owner_id=session.teacher_id,
                actor_id=actor_id,
                request_id=request_id,
                regenerate=regenerate,
                started_at=run.started_at,
            )


async def finish_failure(factory, run_id, status, code):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id)
        if run is not None and run.status == "running":
            run.status, run.error_code, run.finished_at = status, code, now()
            run.outcome_json = json.dumps(dict(status=status, error_code=code))
            await db.commit()


async def execute_analysis(factory, execution):
    run_id = execution["run_id"]
    try:
        remaining = (
            RUN_SECONDS
            - (
                now() - execution["started_at"].replace(tzinfo=timezone.utc)
            ).total_seconds()
        )
        async with asyncio.timeout(max(0, remaining)):
            result = await analysis_pipeline.run_llm_pipeline(
                execution["session_id"],
                execution["all_messages"],
                execution["teacher_messages"],
                execution["snapshot"],
                factory,
                execution["owner_id"],
                execution["actor_id"],
                request_id=execution["request_id"],
                run_id=run_id,
            )
            async with factory() as db:
                await save_analysis(
                    execution["session_id"],
                    result,
                    db,
                    regenerate=execution["regenerate"],
                    run_id=run_id,
                )
    except asyncio.CancelledError:
        await finish_failure(factory, run_id, "interrupted", "interrupted")
    except TimeoutError:
        await finish_failure(factory, run_id, "failed", "timeout_total")
    except (HTTPException, InvocationError) as error:
        await finish_failure(
            factory,
            run_id,
            "failed",
            (
                error.code
                if isinstance(error, InvocationError)
                else "configuration_unavailable"
            ),
        )
    except Exception:
        logger.exception("Analysis execution failed")
        await finish_failure(factory, run_id, "failed", "storage_failed")


def start_analysis(factory, execution):
    run_id = execution["run_id"]
    task = asyncio.create_task(execute_analysis(factory, execution))
    active_analyses[run_id] = task

    def done(task):
        active_analyses.pop(run_id, None)
        if task.cancelled():
            cleanup = asyncio.create_task(
                finish_failure(factory, run_id, "interrupted", "interrupted")
            )
            cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(cleaned)
        elif task.exception() is not None:
            logger.error("Analysis state cleanup failed")

    task.add_done_callback(done)


def cleaned(task):
    cleanup_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Analysis state cleanup failed")


async def stop_analyses():
    tasks = list(active_analyses.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)


async def request_analysis(
    db, session_id, actor_id, request_id, *, regenerate=False, plan_hash=None
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.commit()
    accepted, execution = await reserve_analysis(
        factory,
        session_id,
        actor_id,
        request_id,
        regenerate=regenerate,
        plan_hash=plan_hash,
    )
    if execution is not None:
        start_analysis(factory, execution)
    from fastapi.responses import JSONResponse

    return JSONResponse(
        accepted,
        status_code=(
            202
            if execution is not None
            or accepted.get("latest_run", {}).get("status") == "running"
            else 200
        ),
    )


async def get_run(db, session_id, run_id):
    run = await db.get(GenerationRun, run_id)
    if (
        run is None
        or run.session_id != session_id
        or run.operation != "analysis"
    ):
        raise HTTPException(404, detail="Run not found")
    return run


async def cancel_analysis(db, session_id, run_id, actor_id, *, admin=False):
    await db.commit()
    await db.execute(text("BEGIN IMMEDIATE"))
    user = await db.get(User, actor_id, populate_existing=True)
    session = await db.get(Session, session_id, populate_existing=True)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, detail="Session not found")
    if (
        user is None
        or (admin and not user.is_admin)
        or (not admin and session.teacher_id != actor_id)
    ):
        raise HTTPException(403, detail="Forbidden")
    run = await get_run(db, session_id, run_id)
    if run.status == "running":
        run.status, run.error_code, run.finished_at = (
            "cancelled",
            "cancelled",
            now(),
        )
        run.outcome_json = json.dumps(
            dict(status="cancelled", error_code="cancelled")
        )
    await db.commit()
    task = active_analyses.get(run_id)
    if task is not None:
        task.cancel()
    return await run_response(db, run, admin=admin)

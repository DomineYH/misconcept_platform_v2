"""Authenticated student generation and persisted run lookup."""

import asyncio
from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_current_user, get_db_session
from src.api.routes.session_helpers import load_session
from src.api.routes.session_messages import limiter
from src.api.schemas import GenerationRequest
from src.config import config
from src.demo.database import DemoError
from src.demo.lifecycle import TaskControl
from src.i18n import display_language, translate
from src.models import GenerationRun, User
from src.services.generation_runs import (
    DemoBinding,
    reserve_student,
    snapshot,
)
from src.services.mentor_generation import reserve_mentor
from src.services.mentor_stream import mentor_response
from src.services.student_stream import DemoRun, StudentStreamingResponse

router = APIRouter(tags=["Sessions"])


def demo_binding(request, ctx):
    """Server-owned demo inputs from app state; registers nothing."""
    # Demo only: normal never loads #197's seed inputs or #195's helper.
    # ponytail: PR4 verifies this binding against the real seed end to end.
    from src.demo.seed import INPUTS
    from src.services.model_configuration import supported_config_sha256

    return DemoBinding(
        ctx,
        request.app.state.demo_request_db.lifecycle,
        request.app.state.demo_seed,
        INPUTS,
        supported_config_sha256,
    )


async def register_demo_run(request):
    """§6: register before reserving so the run's lease outlives the request."""
    if config.APP_MODE != "demo":
        return None
    binding = demo_binding(request, request.state.demo_context)
    cancelled = asyncio.Event()
    token = await binding.registry.register(
        binding.ctx,
        "run",
        str(uuid4()),
        TaskControl(cancelled.set, asyncio.Event()),
    )
    return DemoRun(
        replace(binding, ctx=replace(binding.ctx, lease=token.registry_lease)),
        token,
        cancelled,
    )


def read_binding(request):
    """Read-only binding for demo run JSON; normal mode gets None."""
    if config.APP_MODE != "demo":
        return None
    return demo_binding(request, request.state.demo_context)


# §11: pre-SSE lowercase codes -> (status, retryable, recovery key).
DEMO_ERRORS = {
    "configuration_unavailable": (422, False, "demo.unavailable"),
    "call_limit_reached": (429, True, "demo.retry_later"),
    "context_limit": (422, False, "demo.supported_only"),
    "output_limit": (422, False, "demo.supported_only"),
    "session_ended": (409, False, "demo.stopped"),
}
# Legacy reservation codes without a §11 row become their boundary code.
DEMO_CODES = {
    "student_busy": "BUSY",
    "mentor_busy": "BUSY",
    "unresolved_turn": "REQUEST_CONFLICT",
}


def demo_error_response(error, ctx):
    """The §11 E(code) envelope for a known demo reserve refusal, else None."""
    code = error.detail.get("code") if isinstance(error.detail, dict) else None
    if ctx is None or code not in DEMO_ERRORS.keys() | DEMO_CODES.keys():
        return None
    if code in DEMO_CODES:
        boundary = DemoError(DEMO_CODES[code])
        code, status, retryable, recovery = (
            boundary.code,
            boundary.status_code,
            boundary.retryable,
            boundary.recovery,
        )
    else:
        status, retryable, recovery = DEMO_ERRORS[code]
    return JSONResponse(
        {
            "detail": {
                "code": code,
                "retryable": retryable,
                "recovery": recovery,
                "generation": ctx.generation,
                "auth_epoch": ctx.auth_epoch,
            }
        },
        status_code=status,
        headers=error.headers,
    )


async def reserve_demo_run(run, reserve, **kwargs):
    """Reserve with the run's ID; a refusal or replay ends the registration."""
    try:
        accepted, execution = await reserve(
            **kwargs,
            **(
                {}
                if run is None
                else dict(demo=run.binding, run_id=run.token.key[2])
            ),
        )
    except BaseException:
        if run is not None:
            await run.release()
        raise
    if execution is None and run is not None:
        await run.release()
    return accepted, execution


class MentorRequest(GenerationRequest):
    trigger: Literal["manual", "auto"]


class StudentRequest(GenerationRequest):
    content: str = Field(min_length=1, max_length=5000)
    turn_id: str | None = None

    @field_validator("turn_id")
    @classmethod
    def turn_uuid_string(cls, value):
        return cls.uuid_string(value)


@router.post("/sessions/{session_id}/turns/{turn_id}/mentor/stream")
@limiter.limit("30/minute")
async def mentor_turn(
    request: Request,
    session_id: int,
    turn_id: UUID,
    body: MentorRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await db.close()
    run = await register_demo_run(request)
    factory = (
        async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
        if run is None
        else run.binding.ctx.factory
    )
    try:
        accepted, execution = await reserve_demo_run(
            run,
            reserve_mentor,
            factory=factory,
            session_id=session_id,
            turn_id=str(turn_id),
            user=user,
            request_id=body.request_id,
            trigger=body.trigger,
            language=display_language(request),
        )
    except HTTPException as error:
        response = demo_error_response(error, run and run.binding.ctx)
        if response is None:
            raise
        return response
    if execution is None:
        return JSONResponse(accepted)
    execution["display_language"] = display_language(request)
    return mentor_response(factory, accepted, execution, demo_run=run)


@router.post("/sessions/{session_id}/turns/stream")
@limiter.limit("30/minute")
async def student_turn(
    request: Request,
    session_id: int,
    body: StudentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # Authentication's read session is released before reserving or streaming.
    await db.close()
    run = await register_demo_run(request)
    factory = (
        async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
        if run is None
        else run.binding.ctx.factory
    )
    try:
        accepted, kwargs = await reserve_demo_run(
            run,
            reserve_student,
            factory=factory,
            session_id=session_id,
            user=user,
            request=body,
            language=display_language(request),
        )
    except HTTPException as error:
        response = demo_error_response(error, run and run.binding.ctx)
        if response is None:
            raise
        return response
    if kwargs is None:
        return JSONResponse(accepted)
    kwargs["display_language"] = display_language(request)
    return StudentStreamingResponse(factory, accepted, kwargs, demo_run=run)


@router.get("/runs/{run_id}")
async def get_run(
    request: Request,
    run_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    run = await db.get(GenerationRun, str(run_id))
    if run is None or run.comparison_run_id is not None:
        raise HTTPException(
            404,
            detail=translate(
                "teacher.run_not_found", display_language(request)
            ),
        )
    await load_session(
        run.session_id, user, db, language=display_language(request)
    )
    if run.owner_id != user.id:
        raise HTTPException(
            403,
            detail=translate("teacher.forbidden", display_language(request)),
        )
    return await snapshot(db, run, demo=read_binding(request))


@router.get("/sessions/{session_id}/runs")
async def get_request_run(
    request: Request,
    session_id: int,
    request_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await load_session(session_id, user, db, language=display_language(request))
    run = await db.scalar(
        select(GenerationRun).where(
            GenerationRun.session_id == session_id,
            GenerationRun.owner_id == user.id,
            GenerationRun.request_id == str(request_id),
        )
    )
    if run is None:
        raise HTTPException(
            404,
            detail=translate(
                "teacher.run_not_found", display_language(request)
            ),
        )
    return await snapshot(db, run, demo=read_binding(request))

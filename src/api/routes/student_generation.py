"""Authenticated student generation and persisted run lookup."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_current_user, get_db_session
from src.api.routes.session_helpers import load_session
from src.api.routes.session_messages import limiter
from src.api.schemas import GenerationRequest
from src.models import GenerationRun, User
from src.services.generation_runs import reserve_student, snapshot
from src.services.mentor_generation import reserve_mentor
from src.services.mentor_stream import mentor_response
from src.services.student_stream import StudentStreamingResponse

router = APIRouter(tags=["Sessions"])


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
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    accepted, execution = await reserve_mentor(
        factory, session_id, str(turn_id), user, body.request_id, body.trigger
    )
    if execution is None:
        return JSONResponse(accepted)
    return mentor_response(factory, accepted, execution)


@router.post("/sessions/{session_id}/turns/stream")
@limiter.limit("30/minute")
async def student_turn(
    request: Request,
    session_id: int,
    body: StudentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    # Authentication's read session is released before reserving or streaming.
    await db.close()
    accepted, kwargs = await reserve_student(factory, session_id, user, body)
    if kwargs is None:
        return JSONResponse(accepted)
    return StudentStreamingResponse(factory, accepted, kwargs)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    run = await db.get(GenerationRun, str(run_id))
    if run is None:
        raise HTTPException(404, detail="Run not found")
    await load_session(run.session_id, user, db)
    if run.owner_id != user.id:
        raise HTTPException(403, detail="Forbidden")
    return await snapshot(db, run)


@router.get("/sessions/{session_id}/runs")
async def get_request_run(
    session_id: int,
    request_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await load_session(session_id, user, db)
    run = await db.scalar(
        select(GenerationRun).where(
            GenerationRun.session_id == session_id,
            GenerationRun.owner_id == user.id,
            GenerationRun.request_id == str(request_id),
        )
    )
    if run is None:
        raise HTTPException(404, detail="Run not found")
    return await snapshot(db, run)

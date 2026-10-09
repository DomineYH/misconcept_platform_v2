"""Native lesson configuration, immutable reads and teacher-safe projection."""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import Field, StrictInt, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from src.api.routes.session_helpers import validate_scenario_access
from src.api.schemas.scenario_config import ConfigValue, ScenarioConfig
from src.models import Session
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model
from src.services.scenario_publication import publication_errors


class ScenarioContext(ConfigValue):
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(max_length=100)
    target_grade: str = Field(max_length=100)


class LessonSnapshot(ConfigValue):
    schema_version: StrictInt = Field(ge=1, le=1)
    scenario_context: ScenarioContext
    config: ScenarioConfig


def canonical_hash(envelope):
    return hashlib.sha256(
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def configuration_error():
    return HTTPException(400, detail={"code": "configuration_unavailable"})


def read_lesson_snapshot(session):
    """Fail closed: never reconstruct missing/corrupt native lesson inputs."""
    try:
        snapshot = LessonSnapshot.model_validate(session.config_snapshot_json)
        if (
            session.snapshot_origin != "native"
            or session.source_scenario_version is None
            or session.source_scenario_version < 1
            or session.snapshot_created_at is None
            or canonical_hash(session.config_snapshot_json)
            != session.config_hash
            or snapshot.model_dump() != session.config_snapshot_json
            or publication_errors(snapshot.config)
        ):
            raise ValueError("invalid_snapshot")
        return snapshot
    except (ValidationError, ValueError, TypeError, UnicodeError):
        raise configuration_error() from None


def public_lesson(context, config):
    """Only fields allowed to cross the teacher HTML/API boundary."""
    mentor = config.mentor
    enabled = mentor.mode != "off"
    return dict(
        title=context.title,
        subject=context.subject,
        target_grade=context.target_grade,
        problem_situation=config.problem.public_text,
        learning_objective=config.problem.learning_objective,
        student_name=config.student.name,
        student_profile=config.student.public_profile,
        mentor_mode=mentor.mode,
        mentor_name=mentor.name if enabled else "",
        greeting_message=mentor.welcome_message if enabled else "",
    )


def scenario_snapshot(scenario):
    try:
        return LessonSnapshot.model_validate(
            dict(
                schema_version=scenario.config_schema_version,
                scenario_context=dict(
                    title=scenario.title,
                    subject=scenario.subject or "",
                    target_grade=scenario.target_grade or "",
                ),
                config=scenario.config_json,
            )
        )
    except (ValidationError, ValueError, TypeError):
        raise configuration_error() from None


def public_scenario(scenario):
    snapshot = scenario_snapshot(scenario)
    return dict(
        id=scenario.id,
        **public_lesson(snapshot.scenario_context, snapshot.config),
    )


async def start_lesson(db, scenario_id, user, *, reuse=False):
    # Release auth reads before serializing against scenario edits/ACL changes.
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    await db.refresh(user)
    scenario = await validate_scenario_access(scenario_id, user, db)
    if not scenario.is_active:
        raise HTTPException(404, detail="Scenario not found")
    session = None
    if reuse:
        session = await db.scalar(
            select(Session).where(
                Session.scenario_id == scenario_id,
                Session.teacher_id == user.id,
                Session.ended_at.is_(None),
                Session.deleted_at.is_(None),
            )
        )
    if session is None:
        if scenario.status != "published":
            raise HTTPException(400, detail={"code": "scenario_not_published"})
        snapshot = scenario_snapshot(scenario)
        if scenario.review_required or publication_errors(snapshot.config):
            raise configuration_error()
        for role in ("student", "mentor", "analysis"):
            if role == "mentor" and snapshot.config.mentor.mode == "off":
                continue
            try:
                await resolve_frozen_model(
                    db,
                    getattr(snapshot.config, role).resolved_model_config,
                    role,
                )
            except InvocationError:
                raise configuration_error() from None
        envelope = snapshot.model_dump()
        session = Session(
            scenario_id=scenario_id,
            teacher_id=user.id,
            config_snapshot_json=envelope,
            config_hash=canonical_hash(envelope),
            source_scenario_version=scenario.config_version,
            snapshot_origin="native",
            snapshot_created_at=datetime.now(timezone.utc),
        )
        db.add(session)
        try:
            await db.flush()
        except SQLAlchemyError:
            await db.rollback()
            raise HTTPException(
                500,
                detail="대화 세션을 시작할 수 없습니다. 잠시 후 다시 시도해주세요.",
            ) from None
    read_lesson_snapshot(session)
    await db.commit()
    return session

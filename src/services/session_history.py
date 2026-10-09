"""Reconstruction candidates and safe display data, never execution inputs."""

from pydantic import ValidationError
from sqlalchemy import update

from src.api.schemas.scenario_config import ScenarioConfig
from src.models import Session
from src.services.lesson_snapshots import (
    LessonSnapshot,
    canonical_hash,
    configuration_error,
    read_lesson_snapshot,
)


class ReconstructedSnapshot(LessonSnapshot):
    config: ScenarioConfig | None
    unknown_fields: list[str]


UNKNOWN_FIELD_NAMES = {
    "scenario_context": "시나리오 제목·과목·학년",
    "config": "전체 수업 설정",
    "config.problem": "공개 문제·학습목표",
    "config.student": "학생봇 소개·지시·모델·옵션",
    "config.mentor": "멘토 설정",
    "config.analysis": "분석 기준·지시·모델·옵션",
    "config.runtime": "문맥 턴 제한",
}


async def reconstruct_sessions(db, scenario_id, target, created_at):
    """Freeze conversion-time candidates; none proves a historical setting."""
    config = target["config"]
    envelope = ReconstructedSnapshot(
        schema_version=1,
        scenario_context={
            key: target[key] for key in ("title", "subject", "target_grade")
        },
        config=config,
        unknown_fields=["scenario_context"]
        + [
            f"config.{role}.{key}"
            for role, values in config.items()
            for key in values
        ],
    ).model_dump()
    await db.execute(
        update(Session)
        .where(
            Session.scenario_id == scenario_id,
            Session.snapshot_origin.is_(None),
            Session.config_snapshot_json.is_(None),
            Session.config_hash.is_(None),
            Session.snapshot_created_at.is_(None),
            Session.source_scenario_version.is_(None),
        )
        .values(
            config_snapshot_json=envelope,
            config_hash=canonical_hash(envelope),
            snapshot_origin="legacy_reconstructed",
            snapshot_created_at=created_at,
            source_scenario_version=None,
        )
    )


def session_display(session):
    """Allowlisted history fields; unconverted history has no current fallback."""
    origin = session.snapshot_origin
    unknown_fields = []
    title, student_name = "Unknown", ""
    if origin == "native":
        snapshot = read_lesson_snapshot(session)
        title = snapshot.scenario_context.title
        student_name = snapshot.config.student.name
    elif origin == "legacy_reconstructed":
        try:
            snapshot = ReconstructedSnapshot.model_validate(
                session.config_snapshot_json
            )
            if (
                session.source_scenario_version is not None
                or session.snapshot_created_at is None
                or canonical_hash(session.config_snapshot_json)
                != session.config_hash
                or snapshot.model_dump() != session.config_snapshot_json
                or not snapshot.unknown_fields
            ):
                raise ValueError("invalid_reconstruction")
        except (ValidationError, ValueError, TypeError, UnicodeError):
            raise configuration_error() from None
        title = snapshot.scenario_context.title
        student_name = snapshot.config.student.name if snapshot.config else ""
        unknown_fields = snapshot.unknown_fields
    else:
        if origin is not None or session.config_snapshot_json is not None:
            raise configuration_error()
        unknown_fields = ["scenario_context", "config"]
    return dict(
        scenario_title=title,
        student_name=student_name,
        snapshot_provenance=dict(
            snapshot_origin=origin or "legacy_unconverted",
            snapshot_created_at=(
                session.snapshot_created_at.isoformat()
                if session.snapshot_created_at
                else None
            ),
            source_scenario_version=session.source_scenario_version,
            unknown_fields=unknown_fields,
            unknown_field_names=list(
                dict.fromkeys(
                    UNKNOWN_FIELD_NAMES.get(
                        ".".join(field.split(".")[:2]), "그 밖의 설정"
                    )
                    for field in unknown_fields
                )
            ),
            config_hash_kind=(
                "native"
                if origin == "native"
                else (
                    "reconstruction_only"
                    if origin == "legacy_reconstructed"
                    else "unknown"
                )
            ),
        ),
    )

"""Offline, deterministic legacy conversion; artifacts are administrator only."""

from datetime import datetime, timezone

from sqlalchemy import select, text, update

from src.models import (
    ModelConfig,
    ProviderConnection,
    Scenario,
    ScenarioGroup,
)
from src.services.legacy_conversion_values import (
    EffectiveSettings,
    convert_values,
)
from src.services.lesson_snapshots import canonical_hash
from src.services.session_history import reconstruct_sessions

LEGACY_COLUMNS = (
    "id",
    "title",
    "subject",
    "student_name",
    "student_profile",
    "prompt",
    "problem_situation",
    "greeting_message",
    "video_url",
    "video_transcript",
    "chat_model",
    "chat_temperature",
    "tutor_intervention_threshold",
    "tutor_sensitivity",
    "student_template_id",
    "tutor_template_id",
    "framework_id",
    "created_by",
    "created_at",
    "deleted_at",
    "is_active",
    "config_version",
)


async def capture_archive(db):
    """Read the pre-contract schema explicitly; never users/secrets/app settings."""

    def dates(row, keys):
        row = dict(row)
        for key in keys:
            if row[key] is not None:
                row[key] = datetime.fromisoformat(row[key]).isoformat()
        return row

    templates = [
        dates(row, ("created_at", "updated_at"))
        for row in (
            await db.execute(
                text(
                    "SELECT id,bot_type,template_name,template_text,version,created_at,updated_at,updated_by FROM prompt_template ORDER BY id"
                )
            )
        ).mappings()
    ]
    frameworks = [
        dates(row, ("created_at",))
        for row in (
            await db.execute(
                text(
                    "SELECT id,name,description,category_name,labels_json,created_at FROM analysis_framework ORDER BY id"
                )
            )
        ).mappings()
    ]
    template_map = {row["id"]: row for row in templates}
    framework_map = {row["id"]: row for row in frameworks}
    rows = (
        (
            await db.execute(
                text(
                    "SELECT "
                    + ",".join(LEGACY_COLUMNS)
                    + " FROM scenario ORDER BY id"
                )
            )
        )
        .mappings()
        .all()
    )
    sources = []
    for row in rows:
        source = dates(row, ("created_at", "deleted_at"))
        source["groups"] = list(
            (
                await db.scalars(
                    select(ScenarioGroup.group_id)
                    .where(ScenarioGroup.scenario_id == row["id"])
                    .order_by(ScenarioGroup.group_id)
                )
            ).all()
        )
        source["student_template"] = template_map.get(
            row["student_template_id"]
        )
        source["mentor_template"] = template_map.get(row["tutor_template_id"])
        source["framework"] = framework_map.get(row["framework_id"])
        sources.append(source)
    return dict(
        schema_version=1,
        scenarios=sources,
        prompt_templates=templates,
        analysis_frameworks=frameworks,
    )


async def conversion_manifest(db, archive, effective, archive_ref):
    if archive.get("schema_version") != 1:
        raise ValueError("unsupported_archive_version")
    settings = EffectiveSettings.model_validate(effective)
    models = (
        (
            await db.execute(
                select(
                    ModelConfig.id,
                    ModelConfig.provider_connection_id,
                    ModelConfig.model_id,
                )
                .join(ProviderConnection)
                .where(ProviderConnection.provider == "openai")
            )
        )
        .mappings()
        .all()
    )
    archive_hash = canonical_hash(archive)
    rows = []
    for source in archive["scenarios"]:
        target, reasons, evidence = convert_values(source, settings, models)
        target_hash = canonical_hash(target)
        provenance = [
            dict(
                field="원문 archive",
                source="전체 원문은 관리자 전용 archive에 보존됩니다.",
                target="",
                archive_ref=archive_ref,
                archive_hash=archive_hash,
                source_hash=canonical_hash(source),
                target_hash=target_hash,
                captured_at=settings.captured_at,
                settings_source=settings.source,
            )
        ] + evidence
        rows.append(
            dict(
                id=source["id"],
                source_version=source["config_version"],
                source_hash=canonical_hash(source),
                target_hash=target_hash,
                target=target,
                review_reasons=reasons,
                conversion_provenance=provenance,
            )
        )
    return dict(
        schema_version=1,
        archive_hash=archive_hash,
        archive_ref=archive_ref,
        effective_settings=effective,
        scenarios=rows,
    )


async def apply_manifest(db, archive, manifest):
    """Apply reviewed candidates atomically; existing native data is never written."""
    if manifest != await conversion_manifest(
        db,
        archive,
        manifest["effective_settings"],
        manifest["archive_ref"],
    ):
        raise ValueError("manifest_mismatch")
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    current = {
        row["id"]: row for row in (await capture_archive(db))["scenarios"]
    }
    results = []
    created_at = datetime.now(timezone.utc)
    for row in manifest["scenarios"]:
        scenario = await db.get(Scenario, row["id"], populate_existing=True)
        if scenario is None:
            status = "conflict"
        elif scenario.config_json is not None:
            actual = dict(
                title=scenario.title,
                subject=scenario.subject or "",
                target_grade=scenario.target_grade or "",
                config=scenario.config_json,
            )
            status = (
                "skipped"
                if canonical_hash(actual) == row["target_hash"]
                and scenario.conversion_provenance_json
                == row["conversion_provenance"]
                and scenario.config_version == row["source_version"] + 1
                else "conflict"
            )
        elif canonical_hash(current[row["id"]]) != row["source_hash"]:
            status = "conflict"
        else:
            target = row["target"]
            await db.execute(
                update(Scenario)
                .where(Scenario.id == row["id"], Scenario.config_json.is_(None))
                .values(
                    title=target["title"],
                    subject=(
                        target["subject"]
                        if scenario.subject is not None
                        else None
                    ),
                    target_grade=target["target_grade"],
                    config_json=target["config"],
                    config_schema_version=1,
                    config_version=Scenario.config_version + 1,
                    status="draft",
                    review_required=True,
                    review_reasons=row["review_reasons"],
                    conversion_provenance_json=row["conversion_provenance"],
                )
            )
            status = "converted"
        results.append(dict(id=row["id"], status=status))
        if status in {"converted", "skipped"}:
            await reconstruct_sessions(db, row["id"], row["target"], created_at)
    await db.flush()
    return results

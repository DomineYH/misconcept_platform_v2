"""Offline, deterministic legacy conversion; artifacts are administrator only."""

import json
from hashlib import sha256

from sqlalchemy import select, text, update

from src.models import (
    AnalysisFramework,
    ModelConfig,
    PromptTemplate,
    ProviderConnection,
    Scenario,
    ScenarioGroup,
)
from src.services.legacy_conversion_values import (
    EffectiveSettings,
    convert_values,
)

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


def checksum(value):
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


async def capture_archive(db):
    """Allowlisted source only: never read users, credentials or app settings."""
    rows = (
        (
            await db.execute(
                select(
                    *(getattr(Scenario, key) for key in LEGACY_COLUMNS)
                ).order_by(Scenario.id)
            )
        )
        .mappings()
        .all()
    )
    sources = []
    for row in rows:
        source = dict(row)
        for key in ("created_at", "deleted_at"):
            if source[key] is not None:
                source[key] = source[key].isoformat()
        source["groups"] = list(
            (
                await db.scalars(
                    select(ScenarioGroup.group_id)
                    .where(ScenarioGroup.scenario_id == row["id"])
                    .order_by(ScenarioGroup.group_id)
                )
            ).all()
        )
        for role, key in (
            ("student", "student_template_id"),
            ("mentor", "tutor_template_id"),
        ):
            template = (
                await db.get(PromptTemplate, row[key], populate_existing=True)
                if row[key]
                else None
            )
            source[role + "_template"] = (
                None
                if template is None
                else dict(
                    id=template.id,
                    bot_type=template.bot_type,
                    version=template.version,
                    template_name=template.template_name,
                    template_text=template.template_text,
                    updated_by=template.updated_by,
                    created_at=template.created_at.isoformat(),
                    updated_at=template.updated_at.isoformat(),
                )
            )
        framework = (
            await db.get(
                AnalysisFramework, row["framework_id"], populate_existing=True
            )
            if row["framework_id"]
            else None
        )
        source["framework"] = (
            None
            if framework is None
            else dict(
                id=framework.id,
                name=framework.name,
                description=framework.description,
                category_name=framework.category_name,
                labels_json=framework.labels_json,
                created_at=framework.created_at.isoformat(),
            )
        )
        sources.append(source)
    return dict(schema_version=1, scenarios=sources)


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
    archive_hash = checksum(archive)
    rows = []
    for source in archive["scenarios"]:
        target, reasons, evidence = convert_values(source, settings, models)
        target_hash = checksum(target)
        provenance = [
            dict(
                field="원문 archive",
                source="전체 원문은 관리자 전용 archive에 보존됩니다.",
                target="",
                archive_ref=archive_ref,
                archive_hash=archive_hash,
                source_hash=checksum(source),
                target_hash=target_hash,
                captured_at=settings.captured_at,
                settings_source=settings.source,
            )
        ] + evidence
        rows.append(
            dict(
                id=source["id"],
                source_version=source["config_version"],
                source_hash=checksum(source),
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
                if checksum(actual) == row["target_hash"]
                and scenario.conversion_provenance_json
                == row["conversion_provenance"]
                and scenario.config_version == row["source_version"] + 1
                else "conflict"
            )
        elif checksum(current[row["id"]]) != row["source_hash"]:
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
    await db.flush()
    return results

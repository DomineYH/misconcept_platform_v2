"""Native authoring reads and atomic revision-aware saves and publication."""

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update

from src.models import (
    ModelConfig,
    ProviderConnection,
    Scenario,
    ScenarioGroup,
    UserGroup,
)
from src.models.provider_connection import now
from src.services.admin_scenario_ops import soft_delete_scenario_record
from src.services.model_verification import model_available
from src.services.scenario_publication import publication_errors, review_reasons


def field_error(path, code):
    return HTTPException(
        422,
        detail=[
            {"path": path, "code": code, "message": "입력값을 확인하세요."}
        ],
    )


async def native_scenario(db, scenario_id, expected_version=None):
    scenario = await db.get(Scenario, scenario_id)
    if (
        scenario is not None
        and expected_version is not None
        and scenario.config_version != expected_version
    ):
        raise HTTPException(
            409,
            detail={
                "code": "version_conflict",
                "current_version": scenario.config_version,
            },
        )
    if scenario is None or scenario.deleted_at is not None:
        raise HTTPException(404, detail="Scenario not found")
    if scenario.config_json is None:
        raise field_error("config", "legacy_conversion_required")
    return scenario


async def draft_view(db, scenario):
    groups = (
        await db.scalars(
            select(ScenarioGroup.group_id)
            .where(ScenarioGroup.scenario_id == scenario.id)
            .order_by(ScenarioGroup.group_id)
        )
    ).all()
    return dict(
        id=scenario.id,
        title=scenario.title,
        subject=scenario.subject or "",
        target_grade=scenario.target_grade or "",
        is_active=bool(scenario.is_active),
        groups=groups,
        status=scenario.status,
        config_schema_version=scenario.config_schema_version,
        config_version=scenario.config_version,
        config=scenario.config_json,
        review_required=scenario.review_required,
        review_reasons=scenario.review_reasons,
        conversion_provenance=scenario.conversion_provenance_json,
    )


async def save_scenario(db, data, user_id, scenario_id=None):
    # Auth reads must not retain a SQLite read transaction while taking the writer.
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    scenario = None
    if scenario_id is not None:
        scenario = await native_scenario(db, scenario_id, data.expected_version)
    errors = publication_errors(data.config)
    if len(set(data.groups)) != len(data.groups) or set(data.groups) != set(
        (
            await db.scalars(
                select(UserGroup.id).where(UserGroup.id.in_(data.groups))
            )
        ).all()
    ):
        raise field_error("groups", "invalid_groups")
    for role in ("student", "mentor", "analysis"):
        selection = getattr(data.config, role).resolved_model_config
        if selection is None:
            continue
        model = await db.get(ModelConfig, selection.model_config_id)
        connection = (
            await db.get(ProviderConnection, model.provider_connection_id)
            if model
            else None
        )
        if (
            not model
            or not connection
            or (
                selection.provider_connection_id != connection.id
                or selection.provider != connection.provider
                or selection.model_id != model.model_id
            )
        ):
            raise field_error(
                f"config.{role}.resolved_model_config",
                "model_identity_mismatch",
            )
        if (
            role != "mentor" or data.config.mentor.mode != "off"
        ) and not model_available(
            model,
            connection,
            role,
            selection.options.model_dump(exclude_unset=True),
        ):
            errors.extend(
                field_error(
                    f"config.{role}.resolved_model_config", "model_unavailable"
                ).detail
            )
    reasons = (
        review_reasons(
            scenario,
            data.model_dump(
                include={"config", "title", "subject", "target_grade"}
            ),
            errors,
        )
        if scenario and scenario.review_required
        else []
    )
    if data.action == "publish":
        blockers = [
            dict(path=r["path"], code=r["code"], message=r["message"])
            for r in reasons
            if r["blocking"]
        ]
        if blockers or errors:
            raise HTTPException(422, detail=blockers or errors)
        if reasons and not data.acknowledge_review:
            raise field_error(
                "acknowledge_review", "review_acknowledgement_required"
            )
        reasons = []
    values = dict(
        title=data.title,
        subject=data.subject,
        target_grade=data.target_grade,
        config_schema_version=data.config_schema_version,
        config_json=data.config.model_dump(),
        status="published" if data.action == "publish" else "draft",
        review_required=bool(reasons),
        review_reasons=reasons,
        updated_at=now(),
    )
    if scenario_id is None:
        scenario = Scenario(
            **values,
            created_by=user_id,
            config_version=1,
            is_active=int(data.is_active),
        )
        db.add(scenario)
        await db.flush()
        scenario_id, version = scenario.id, 1
    else:
        if "is_active" in data.model_fields_set:
            values["is_active"] = int(data.is_active)
        version = await advance_revision(
            db, scenario_id, data.expected_version, values
        )
        await db.execute(
            delete(ScenarioGroup).where(
                ScenarioGroup.scenario_id == scenario_id
            )
        )
    db.add_all(
        [
            ScenarioGroup(scenario_id=scenario_id, group_id=group)
            for group in data.groups
        ]
    )
    await db.flush()
    return dict(
        id=scenario_id,
        version=version,
        status=values["status"],
        review_required=values["review_required"],
        review_reasons=reasons,
    )


async def advance_revision(db, scenario_id, expected_version, values):
    row = (
        await db.execute(
            update(Scenario)
            .where(
                Scenario.id == scenario_id,
                Scenario.config_version == expected_version,
                Scenario.deleted_at.is_(None),
            )
            .values(**values, config_version=Scenario.config_version + 1)
            .returning(Scenario.config_version)
        )
    ).first()
    if not row:
        current = await db.scalar(
            select(Scenario.config_version).where(Scenario.id == scenario_id)
        )
        raise HTTPException(
            409, detail={"code": "version_conflict", "current_version": current}
        )
    return row[0]


async def delete_draft(db, scenario_id, expected_version, user_id, logger):
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    await native_scenario(db, scenario_id, expected_version)
    version = await advance_revision(
        db, scenario_id, expected_version, {"updated_at": now()}
    )
    result = await soft_delete_scenario_record(db, scenario_id, user_id, logger)
    return dict(result, version=version)

"""Public model/settings views and revision-aware role availability."""

import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from src.api.schemas.ai_settings import CallLimits, CallTimeouts
from src.api.schemas.scenario_config import ScenarioConfig
from src.i18n import translate
from src.models.model_config import ROLES, AppSetting, ModelConfig
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_verification import effective_roles, model_available
from src.services.role_probe_contract import probe_options

# Contract §16.1: the only fields of the supported execution content.
PROJECTION = dict(
    problem=("public_text", "learning_objective"),
    student=(
        "name",
        "public_profile",
        "internal_profile",
        "misconception",
        "behavior_instruction",
    ),
    mentor=("mode", "name", "welcome_message", "behavior_instruction"),
    analysis=(
        "context",
        "expected_understanding",
        "instruction",
        "rubric_name",
        "rubric_description",
        "category_name",
        "classification_enabled",
    ),
)
POLICY_FIELDS = (
    "condition",
    "start_turn",
    "min_interval_turns",
    "window_turns",
    "max_interventions",
)
RUBRIC_FIELDS = ("id", "name", "criteria", "level")


def supported_config_projection(config_schema_version, config):
    if type(config_schema_version) is not int or config_schema_version != 1:
        raise ValueError("INVALID_REQUEST")
    try:
        values = ScenarioConfig.model_validate(config).model_dump()
    except ValidationError:
        raise ValueError("INVALID_REQUEST") from None
    projected = {
        section: {name: values[section][name] for name in names}
        for section, names in PROJECTION.items()
    }
    projected["mentor"]["intervention_policy"] = {
        name: values["mentor"]["intervention_policy"][name]
        for name in POLICY_FIELDS
    }
    projected["analysis"]["rubric"] = [
        {name: item[name] for name in RUBRIC_FIELDS}
        for item in values["analysis"]["rubric"]
    ]
    for role in ROLES:
        selection = values[role]["resolved_model_config"]
        # PKs and options are excluded here but still validated separately.
        projected[role]["resolved_model_config"] = selection and {
            "provider": selection["provider"],
            "model_id": selection["model_id"],
        }
    return dict(config_schema_version=1, config=projected)


def supported_config_sha256(config_schema_version, config):
    projection = supported_config_projection(config_schema_version, config)
    return hashlib.sha256(
        json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def settings_values(setting):
    try:
        return (
            CallLimits.model_validate(setting.limits_json).model_dump(),
            CallTimeouts.model_validate(setting.timeouts_json).model_dump(),
        )
    except ValidationError:
        raise HTTPException(
            503, detail={"code": "configuration_unavailable"}
        ) from None


def connection_impact(
    provider,
    models,
    settings,
    scenarios,
    language="ko",
):
    registered = [m for m in models if m["provider"] == provider]
    impact = [
        translate(
            "admin.registered_model_description",
            language,
            display_name=m["display_name"],
            model_id=m["model_id"],
        )
        for m in registered
    ]
    by_id = {m["id"]: m for m in registered}
    if settings:
        for role, default in settings["defaults"].items():
            if default and default["model_config_id"] in by_id:
                impact.append(
                    translate(
                        "admin.role_default_authoring_model",
                        language,
                        role=role,
                        model_name=by_id[default["model_config_id"]][
                            "display_name"
                        ],
                    )
                )
    for scenario in scenarios:
        config = scenario.config_json
        if not config:
            continue
        state = (
            translate("admin.publish", language)
            if scenario.status == "published"
            else translate("admin.draft", language)
        )
        state += (
            " · " + translate("admin.active", language)
            if scenario.is_active
            else " · " + translate("admin.inactive", language)
        )
        for role in ROLES:
            role_config = config[role]
            selection = role_config["resolved_model_config"]
            if not selection or selection["provider"] != provider:
                continue
            disabled = role == "mentor" and role_config["mode"] == "off"
            execution = (
                translate("admin.mentor_off_excluded_from_execution", language)
                if disabled
                else (
                    translate("admin.used_for_new_lessons", language)
                    if scenario.status == "published" and scenario.is_active
                    else translate("admin.excluded_from_new_lessons", language)
                )
            )
            impact.append(
                translate(
                    "admin.scenario_model_usage",
                    language,
                    scenario_id=scenario.id,
                    title=scenario.title,
                    role=role,
                    model_id=selection["model_id"],
                    state=state,
                    execution=execution,
                )
            )
    return impact


def public_model(
    model, connection, language="ko", *, app_mode: Literal["normal", "demo"]
):
    definition = capabilities(connection.provider, model.model_id, language)
    if definition:
        definition["metadata_conflict"] = metadata_conflict(
            model.model_id, connection
        )
    budgets = {}
    if definition and connection.provider in ("openai", "anthropic", "google"):
        for role in ROLES:
            try:
                budgets[role] = probe_options(connection.provider, model, role)[
                    "max_output_tokens"
                ]
            except ValueError:
                pass
    return dict(
        id=model.id,
        provider=connection.provider,
        model_id=model.model_id,
        display_name=model.display_name,
        enabled=model.enabled,
        config_version=model.config_version,
        capabilities=definition,
        default_options=model.default_options_json,
        verification_state=effective_roles(
            model, connection, app_mode=app_mode
        ),
        probe_budgets=budgets,
    )


async def configuration_state(
    db, connections, language="ko", *, app_mode: Literal["normal", "demo"]
):
    by_id = {c.id: c for c in connections.values()}
    models = (
        await db.scalars(select(ModelConfig).order_by(ModelConfig.id))
    ).all()
    setting = await db.get(AppSetting, 1)
    settings = None
    if setting:
        limits, timeouts = settings_values(setting)
        defaults = {}
        by_model_id = {m.id: m for m in models}
        for role in ROLES:
            model_id = getattr(setting, f"{role}_model_config_id")
            model = by_model_id.get(model_id)
            defaults[role] = (
                None
                if model_id is None
                else dict(
                    model_config_id=model_id,
                    available=bool(
                        model
                        and model_available(
                            model,
                            by_id[model.provider_connection_id],
                            role,
                            app_mode=app_mode,
                        )
                    ),
                )
            )
        if app_mode == "normal":
            # The normal administrator form has no demo limit field.
            del limits["demo"]
        settings = dict(
            settings_version=setting.settings_version,
            defaults=defaults,
            limits=limits,
            timeouts=timeouts,
        )
    return dict(
        models=[
            public_model(
                m, by_id[m.provider_connection_id], language, app_mode=app_mode
            )
            for m in models
        ],
        models_available=True,
        probes_available=True,
        probe_roles=list(ROLES),
        settings=settings,
    )

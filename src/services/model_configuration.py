"""Public model/settings views and revision-aware role availability."""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from src.api.schemas.ai_settings import CallLimits, CallTimeouts
from src.i18n import translate
from src.models.model_config import ROLES, AppSetting, ModelConfig
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_verification import effective_roles, model_available
from src.services.role_probe_contract import probe_options


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


def public_model(model, connection, language="ko"):
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
        verification_state=effective_roles(model, connection),
        probe_budgets=budgets,
    )


async def configuration_state(db, connections, language="ko"):
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
                            model, by_id[model.provider_connection_id], role
                        )
                    ),
                )
            )
        settings = dict(
            settings_version=setting.settings_version,
            defaults=defaults,
            limits=limits,
            timeouts=timeouts,
        )
    return dict(
        models=[
            public_model(m, by_id[m.provider_connection_id], language)
            for m in models
        ],
        models_available=True,
        probes_available=True,
        probe_roles=list(ROLES),
        settings=settings,
    )

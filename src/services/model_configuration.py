"""Public model/settings views and revision-aware role availability."""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from src.api.schemas.ai_settings import CallLimits, CallTimeouts
from src.models.model_config import ROLES, AppSetting, ModelConfig
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_verification import effective_roles, model_available
from src.services.student_probe_contract import probe_options


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


def connection_impact(provider, models, settings):
    registered = [m for m in models if m["provider"] == provider]
    impact = [
        f"등록 모델: {m['display_name']} ({m['model_id']})" for m in registered
    ]
    by_id = {m["id"]: m for m in registered}
    if settings:
        for role, default in settings["defaults"].items():
            if default and default["model_config_id"] in by_id:
                impact.append(
                    f"{role} 작성 기본 모델: {by_id[default['model_config_id']]['display_name']}"
                )
    return impact


def public_model(model, connection):
    definition = capabilities(connection.provider, model.model_id)
    if definition:
        definition["metadata_conflict"] = metadata_conflict(
            model.model_id, connection
        )
    budgets = {}
    if definition and connection.provider == "openai":
        try:
            budgets["student"] = probe_options(connection.provider, model)[
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


async def configuration_state(db, connections):
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
            public_model(m, by_id[m.provider_connection_id]) for m in models
        ],
        models_available=True,
        probes_available=True,
        probe_roles=["student"],
        settings=settings,
    )

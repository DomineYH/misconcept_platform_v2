"""Administrator singleton settings writes without secret reauthentication."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_models import commit_configuration
from src.api.routes.admin_providers import CredentialRoute
from src.api.schemas.ai_settings import SettingsUpdate
from src.models.model_config import AppSetting, ModelConfig
from src.models.provider_connection import ProviderConnection, now
from src.models.user import User
from src.services.call_admission import execution_lock
from src.services.model_verification import model_available

router = APIRouter(tags=["Admin AI Settings"], route_class=CredentialRoute)


@router.post("/admin/ai/settings/update")
async def update_settings(
    data: SettingsUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    async with execution_lock():
        await db.rollback()
        await db.execute(text("BEGIN IMMEDIATE"))
        setting = await db.get(AppSetting, 1)
        if setting is None:
            raise HTTPException(503, detail={"code": "storage_unavailable"})
        if setting.settings_version != data.expected_version:
            raise HTTPException(409, detail={"code": "version_conflict"})
        values = {}
        for role, model_id in data.defaults.model_dump().items():
            field = f"{role}_model_config_id"
            if model_id is not None and model_id != getattr(setting, field):
                model = await db.get(ModelConfig, model_id)
                connection = (
                    await db.get(
                        ProviderConnection, model.provider_connection_id
                    )
                    if model
                    else None
                )
                if model is None or not model_available(
                    model, connection, role
                ):
                    raise HTTPException(
                        422, detail={"code": "default_model_unavailable"}
                    )
            values[field] = model_id
        return await commit_configuration(
            db,
            update(AppSetting)
            .where(
                AppSetting.id == 1,
                AppSetting.settings_version == data.expected_version,
            )
            .values(
                **values,
                limits_json=data.limits.model_dump(),
                timeouts_json=data.timeouts.model_dump(),
                settings_version=data.expected_version + 1,
                updated_at=now(),
            ),
        )

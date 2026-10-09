"""Administrator registration and editing of model configuration."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationInfo,
    field_validator,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_providers import CredentialRoute, Provider
from src.models.model_config import ModelConfig
from src.models.provider_connection import ProviderConnection, now
from src.models.user import User
from src.services.model_capabilities import (
    capabilities,
    metadata_conflict,
    normalize_model_id,
    validate_model_and_options,
)
from src.services.model_verification import effective_roles

router = APIRouter(tags=["Admin Models"], route_class=CredentialRoute)


class ModelRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider: Provider
    model_id: str
    display_name: str

    @field_validator("model_id")
    @classmethod
    def valid_id(cls, value, info: ValidationInfo):
        return normalize_model_id(value, provider=info.data.get("provider"))

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("display_name_required")
        value.encode("utf-8")
        return value.strip()


@router.post("/admin/ai/models")
async def register_model(
    data: ModelRegistration,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    connection = await db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == data.provider
        )
    )
    if connection is None:
        raise HTTPException(503, detail={"code": "storage_unavailable"})
    definition = capabilities(data.provider, data.model_id)
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id=data.model_id,
        display_name=data.display_name,
        capabilities_json=definition,
        capability_definition_version=(
            definition["definition_version"] if definition else None
        ),
    )
    db.add(model)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail={"code": "model_exists"}) from None
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            503, detail={"code": "storage_unavailable"}
        ) from None
    return {"status": "saved"}


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_version: StrictInt = Field(gt=0)
    display_name: str
    enabled: StrictBool
    default_options: dict

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value):
        return ModelRegistration.nonempty_name(value)


async def commit_configuration(db, statement):
    try:
        result = await db.execute(
            statement.execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(409, detail={"code": "version_conflict"})
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            503, detail={"code": "storage_unavailable"}
        ) from None
    return {"status": "saved"}


@router.post("/admin/ai/models/{model_id}/update")
async def update_model(
    model_id: int,
    data: ModelUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    model = await db.get(ModelConfig, model_id)
    if model is None:
        raise HTTPException(404, detail={"code": "model_not_found"})
    if model.config_version != data.expected_version:
        raise HTTPException(409, detail={"code": "version_conflict"})
    connection = await db.get(ProviderConnection, model.provider_connection_id)
    definition = capabilities(connection.provider, model.model_id)
    try:
        options = (
            {}
            if definition is None and not data.default_options
            else validate_model_and_options(
                connection.provider, model.model_id, data.default_options
            )
        )
    except ValueError:
        raise HTTPException(422, detail={"code": "invalid_options"}) from None
    if data.enabled and (
        definition is None
        or metadata_conflict(model.model_id, connection)
        or not any(
            v["status"] == "succeeded"
            for v in effective_roles(model, connection).values()
        )
    ):
        raise HTTPException(422, detail={"code": "role_verification_required"})
    return await commit_configuration(
        db,
        update(ModelConfig)
        .where(
            ModelConfig.id == model_id,
            ModelConfig.config_version == data.expected_version,
        )
        .values(
            display_name=data.display_name,
            enabled=data.enabled,
            default_options_json=options,
            capabilities_json=definition,
            capability_definition_version=(
                definition["definition_version"] if definition else None
            ),
            config_version=data.expected_version + 1,
            updated_at=now(),
        ),
    )

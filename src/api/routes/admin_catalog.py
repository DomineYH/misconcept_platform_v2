"""Manual non-generating catalog refresh with revision-fenced cache writes."""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_models import commit_configuration
from src.api.routes.admin_providers import (
    CredentialRoute,
    Provider,
    changed_connection,
)
from src.models.model_config import AppSetting
from src.models.provider_connection import ProviderConnection, now
from src.models.user import User
from src.services.invocation_ledger import finish_attempt, start_attempt
from src.services.invocation_types import InvocationError
from src.services.model_configuration import settings_values
from src.services.openai_catalog import CatalogError, list_models
from src.services.provider_secrets import (
    ProviderSecretUnavailableError,
    decrypt_key,
)

router = APIRouter(tags=["Admin Catalog"], route_class=CredentialRoute)


class CatalogRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_version: StrictInt = Field(gt=0)


@router.post("/admin/ai/providers/{provider}/catalog")
async def refresh_catalog(
    provider: Provider,
    data: CatalogRefresh,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    if provider != "openai":
        raise HTTPException(503, detail={"code": "provider_not_available"})
    connection = await changed_connection(db, provider, data.expected_version)
    if not connection.enabled:
        raise HTTPException(422, detail={"code": "connection_disabled"})
    try:
        secret = decrypt_key(connection)
    except ProviderSecretUnavailableError:
        raise HTTPException(
            503, detail={"code": "configuration_unavailable"}
        ) from None
    setting = await db.get(AppSetting, 1)
    if setting is None:
        raise HTTPException(503, detail={"code": "storage_unavailable"})
    _, timeouts = settings_values(setting)
    owner_id = user.id
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    revision = connection.credential_revision
    statement = update(ProviderConnection).where(
        ProviderConnection.id == connection.id,
        ProviderConnection.credential_revision == revision,
        ProviderConnection.connection_version == data.expected_version,
        ProviderConnection.enabled.is_(True),
    )
    # Release the read transaction before waiting on the provider.
    await db.rollback()
    try:
        entry_id = await start_attempt(
            factory,
            request_id=str(uuid4()),
            owner_id=owner_id,
            provider=provider,
            model=None,
            role=None,
            operation="model_list",
            credential_revision=revision,
        )
    except InvocationError:
        raise HTTPException(
            503, detail={"code": "configuration_unavailable"}
        ) from None
    try:
        models = await list_models(
            secret,
            connect_timeout=timeouts["connect"],
            total_timeout=timeouts["model_list_total"],
        )
        await finish_attempt(factory, entry_id, status="completed")
    except asyncio.CancelledError:
        await asyncio.shield(
            finish_attempt(
                factory, entry_id, status="cancelled", error_code="interrupted"
            )
        )
        raise
    except InvocationError:
        raise HTTPException(
            503, detail={"code": "configuration_unavailable"}
        ) from None
    except CatalogError as error:
        try:
            await finish_attempt(
                factory,
                entry_id,
                status=(
                    "timed_out"
                    if error.code.startswith("timeout_")
                    else "failed"
                ),
                error_code=error.code,
            )
        except InvocationError:
            raise HTTPException(
                503, detail={"code": "configuration_unavailable"}
            ) from None
        await commit_configuration(
            db, statement.values(error_code=error.code, verified_at=None)
        )
        raise HTTPException(503, detail={"code": error.code}) from None
    finally:
        secret = None
    fetched = now()
    return await commit_configuration(
        db,
        statement.values(
            catalog_models_json=models,
            catalog_fetched_at=fetched,
            catalog_credential_revision=revision,
            verified_at=fetched,
            error_code=None,
            updated_at=fetched,
        ),
    )

"""Manual non-generating catalog refresh with revision-fenced cache writes."""

from contextlib import aclosing

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
from src.models.provider_connection import ProviderConnection, now
from src.models.user import User
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError

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
    if provider not in ("openai", "google"):
        raise HTTPException(503, detail={"code": "provider_not_available"})
    connection = await changed_connection(db, provider, data.expected_version)
    if not connection.enabled:
        raise HTTPException(422, detail={"code": "connection_disabled"})
    owner_id = user.id
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    connection_id = connection.id
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
        permit = await admit_call(
            factory,
            connection_id=connection_id,
            owner_id=owner_id,
            operation="model_list",
            expected_connection_version=data.expected_version,
        )
    except InvocationError as error:
        raise HTTPException(
            (
                429
                if error.code == "call_limit_reached"
                else 409 if error.code == "version_conflict" else 503
            ),
            detail={"code": error.code},
            headers=(
                {"Retry-After": "1"}
                if error.code == "call_limit_reached"
                else None
            ),
        ) from None
    async with aclosing(execute_call(permit, kind="catalog")) as events:
        async for terminal in events:
            pass
    if terminal.type != "completed":
        code = terminal.error_code or "configuration_unavailable"
        if code not in (
            "configuration_unavailable",
            "interrupted",
            "call_limit_reached",
        ):
            await commit_configuration(
                db, statement.values(error_code=code, verified_at=None)
            )
        raise HTTPException(503, detail={"code": code})
    models = terminal.models
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

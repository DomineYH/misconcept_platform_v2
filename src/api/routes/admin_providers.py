"""Administrator-only provider snapshots and encrypted credential writes."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    field_validator,
)
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    AuthenticationRequired,
    get_admin_user,
    get_db_session,
)
from src.config import config
from src.models.provider_connection import ProviderAuditLog, ProviderConnection
from src.models.user import User
from src.services.provider_reauthentication import reauthenticate
from src.services.provider_secrets import (
    ProviderSecretUnavailableError,
    decrypt_key,
    encrypt_key,
    master_key,
)

Provider = Literal["openai", "anthropic", "google"]
PROVIDERS = ("openai", "anthropic", "google")


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"code": "invalid_input"}, status_code=422)
            except AuthenticationRequired:
                return JSONResponse(
                    {"code": "authentication_required"}, status_code=401
                )
            except SQLAlchemyError:
                # SQL exceptions can include bound encrypted material.
                return JSONResponse(
                    {"code": "storage_unavailable"}, status_code=503
                )

        return safe


router = APIRouter(tags=["Admin Providers"], route_class=CredentialRoute)


class CredentialChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_version: StrictInt = Field(gt=0)
    current_password: SecretStr


class KeyChange(CredentialChange):
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("API key is required")
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("API key must be UTF-8 text") from None
        return value


class EnabledChange(CredentialChange):
    enabled: StrictBool


def public_connection(provider, connection):
    registered = bool(connection and connection.encrypted_key)
    status, error = "unconfigured", None
    if registered:
        try:
            decrypt_key(connection)
            status = "ready"
        except ProviderSecretUnavailableError:
            status, error = "decryption_failed", "configuration_unavailable"
    return dict(
        provider=provider,
        connection_version=connection.connection_version if connection else 0,
        credential_revision=connection.credential_revision if connection else 0,
        key_registered=registered,
        masked_hint=connection.masked_hint if connection else None,
        enabled=bool(connection and connection.enabled),
        status=status,
        verified_at=connection.verified_at if connection else None,
        error_code=error,
        impact=[],
        catalog={
            "available": False,
            "stale": False,
            "fetched_at": None,
            "models": [],
        },
    )


@router.get("/admin/ai/state")
async def provider_state(
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    connections = {
        c.provider: c
        for c in (await db.scalars(select(ProviderConnection))).all()
    }
    return JSONResponse(
        jsonable_encoder(
            {
                "master_key_available": master_key() is not None,
                "providers": [
                    public_connection(p, connections.get(p)) for p in PROVIDERS
                ],
                "models": [],
                "models_available": False,
                "settings": None,
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/ai/providers/{provider}/key")
async def save_key(
    request: Request,
    provider: Provider,
    data: KeyChange,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    await reauthenticate(request, user, data.current_password)
    connection = await changed_connection(db, provider, data.expected_version)
    try:
        if connection.encrypted_key:
            decrypt_key(connection)
        revision = connection.credential_revision + 1
        ciphertext, nonce = encrypt_key(
            connection, data.api_key.get_secret_value(), revision
        )
    except ProviderSecretUnavailableError:
        raise HTTPException(
            503, detail={"code": "configuration_unavailable"}
        ) from None
    value = data.api_key.get_secret_value()
    values = dict(
        encrypted_key=ciphertext,
        nonce=nonce,
        encryption_key_version=config.PROVIDER_SECRET_ENCRYPTION_KEY_VERSION,
        credential_revision=revision,
        masked_hint="••••" + (value[-4:] if len(value) > 8 else ""),
        enabled=connection.enabled if connection.encrypted_key else True,
        verified_at=None,
        error_code=None,
    )
    return await commit_change(
        db,
        connection,
        user.id,
        data.expected_version,
        values,
        "key_replaced" if connection.encrypted_key else "key_saved",
    )


async def commit_change(
    db, connection, actor_id, expected_version, values, kind
):
    values.update(
        connection_version=expected_version + 1,
        updated_by=actor_id,
        updated_at=datetime.now(timezone.utc),
    )
    try:
        result = await db.execute(
            update(ProviderConnection)
            .where(
                ProviderConnection.id == connection.id,
                ProviderConnection.connection_version == expected_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(409, detail={"code": "version_conflict"})
        db.add(
            ProviderAuditLog(
                actor_id=actor_id,
                provider_connection_id=connection.id,
                provider=connection.provider,
                change_kind=kind,
                previous_credential_revision=connection.credential_revision,
                credential_revision=values.get(
                    "credential_revision", connection.credential_revision
                ),
                previous_connection_version=expected_version,
                connection_version=expected_version + 1,
            )
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            503, detail={"code": "storage_unavailable"}
        ) from None
    return {"status": "saved"}


async def changed_connection(db, provider, version):
    connection = await db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == provider
        )
    )
    if connection is None:
        raise HTTPException(503, detail={"code": "storage_unavailable"})
    if connection.connection_version != version:
        raise HTTPException(409, detail={"code": "version_conflict"})
    return connection


@router.post("/admin/ai/providers/{provider}/enabled")
async def set_enabled(
    request: Request,
    provider: Provider,
    data: EnabledChange,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    await reauthenticate(request, user, data.current_password)
    connection = await changed_connection(db, provider, data.expected_version)
    if data.enabled and not connection.encrypted_key:
        raise HTTPException(422, detail={"code": "key_required"})
    return await commit_change(
        db,
        connection,
        user.id,
        data.expected_version,
        {"enabled": data.enabled},
        "enabled" if data.enabled else "disabled",
    )


@router.post("/admin/ai/providers/{provider}/delete")
async def delete_key(
    request: Request,
    provider: Provider,
    data: CredentialChange,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    await reauthenticate(request, user, data.current_password)
    connection = await changed_connection(db, provider, data.expected_version)
    return await commit_change(
        db,
        connection,
        user.id,
        data.expected_version,
        dict(
            encrypted_key=None,
            nonce=None,
            encryption_key_version=None,
            masked_hint=None,
            enabled=False,
            verified_at=None,
            error_code=None,
            credential_revision=connection.credential_revision + 1,
        ),
        "deleted",
    )

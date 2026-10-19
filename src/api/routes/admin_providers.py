"""Administrator-only provider snapshots and encrypted credential writes."""

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
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
from src.demo.database import DemoError
from src.demo.lifecycle import NORMAL_SCOPE, lifecycle
from src.demo.session import Provenance
from src.i18n import display_language, translate
from src.models.provider_connection import ProviderAuditLog, ProviderConnection
from src.models.scenario import Scenario
from src.models.user import User
from src.services.call_admission import (
    active_connection_count,
    cancel_connection,
    execution_lock,
)
from src.services.model_capabilities import capabilities
from src.services.model_configuration import (
    configuration_state,
    connection_impact,
)
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


async def demo_read_only(user: User = Depends(get_admin_user)):
    """§20: refuse demo writes before body keys, reauth, SDK or reservations."""
    if config.APP_MODE == "demo":
        raise DemoError("ACTION_FORBIDDEN")
    return user


def execution_scope(request):
    """Cancel only the current visitor's tasks; normal keeps its own scope."""
    if config.APP_MODE == "demo":
        return request.state.demo_context.scope
    return NORMAL_SCOPE


def execution_registry(request):
    """Demo tasks live in the app's bound lifecycle, not the normal singleton."""
    if config.APP_MODE == "demo":
        return request.app.state.demo_request_db.lifecycle
    return lifecycle


def request_factory(db):
    """§6: demo work reuses the guarded request session, never db.bind."""

    @asynccontextmanager
    async def session():
        yield db

    return session


@contextmanager
def demo_missing():
    """§20: a missing visitor record is the §11 404, not the normal body."""
    try:
        yield
    except HTTPException as error:
        if config.APP_MODE == "demo" and error.status_code == 404:
            raise DemoError("RESOURCE_NOT_FOUND") from None
        raise


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
            error = connection.error_code
        except ProviderSecretUnavailableError:
            status, error = "decryption_failed", "configuration_unavailable"
    models = []
    for item in connection.catalog_models_json if connection else []:
        definition = capabilities(provider, item["model_id"])
        models.append(
            {
                **item,
                **(
                    {"preview": definition["preview"]}
                    if definition and "preview" in definition
                    else {}
                ),
            }
        )
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
            "available": provider in ("openai", "anthropic", "google"),
            "stale": bool(
                connection
                and (
                    not connection.catalog_fetched_at
                    or connection.catalog_fetched_at.replace(
                        tzinfo=timezone.utc
                    )
                    + timedelta(hours=24)
                    <= datetime.now(timezone.utc)
                    or connection.catalog_credential_revision
                    != connection.credential_revision
                    or error
                    or status != "ready"
                )
            ),
            "fetched_at": connection.catalog_fetched_at if connection else None,
            "models": models,
        },
    )


@router.get("/admin/ai/state")
async def provider_state(
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    connections = {
        c.provider: c
        for c in (await db.scalars(select(ProviderConnection))).all()
    }
    configuration = await configuration_state(
        db, connections, display_language(request), app_mode=config.APP_MODE
    )
    # §20: demo AI settings are seed examples; visitors cannot change them.
    demo = (
        {"provenance": Provenance(record_origin="seed_prepared").model_dump()}
        if config.APP_MODE == "demo"
        else {}
    )
    scenarios = (
        await db.execute(
            select(
                Scenario.id,
                Scenario.title,
                Scenario.status,
                Scenario.is_active,
                Scenario.config_json,
            )
            .where(Scenario.deleted_at.is_(None))
            .order_by(Scenario.id)
        )
    ).all()
    return JSONResponse(
        jsonable_encoder(
            {
                "master_key_available": master_key() is not None,
                "providers": [
                    {
                        **public_connection(p, connections.get(p)),
                        "impact": connection_impact(
                            p,
                            configuration["models"],
                            configuration["settings"],
                            scenarios,
                            display_language(request),
                        )
                        + [
                            translate(
                                "admin.active_call_count",
                                display_language(request),
                                count=active_connection_count(p),
                            )
                        ],
                    }
                    for p in PROVIDERS
                ],
                **configuration,
                **demo,
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/ai/providers/{provider}/key")
async def save_key(
    request: Request,
    provider: Provider,
    data: KeyChange,
    user: User = Depends(demo_read_only),
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
    async with execution_lock():
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
            if kind in ("disabled", "deleted"):
                cancel_connection(connection.id)
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
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    await reauthenticate(request, user, data.current_password)
    connection = await changed_connection(db, provider, data.expected_version)
    if data.enabled and not connection.encrypted_key:
        raise HTTPException(422, detail={"code": "key_required"})
    if data.enabled:
        try:
            decrypt_key(connection)
        except ProviderSecretUnavailableError:
            raise HTTPException(
                503, detail={"code": "configuration_unavailable"}
            ) from None
    return await commit_change(
        db,
        connection,
        user.id,
        data.expected_version,
        {"enabled": data.enabled, "verified_at": None, "error_code": None},
        "enabled" if data.enabled else "disabled",
    )


@router.post("/admin/ai/providers/{provider}/delete")
async def delete_key(
    request: Request,
    provider: Provider,
    data: CredentialChange,
    user: User = Depends(demo_read_only),
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

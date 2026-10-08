"""Atomic admission and revocation for the single asynchronous worker."""

import asyncio
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from src.models import AppSetting, ModelConfig, ModelProbe, ProviderConnection
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_configuration import settings_values
from src.services.model_verification import (
    ROLE_CONTRACT_VERSIONS,
    model_available,
)
from src.services.probe_lifecycle import current_probe
from src.services.provider_secrets import (
    ProviderSecretUnavailableError,
    decrypt_key,
)

# ponytail: process-local admission requires one worker; revisit before scaling.
_locks = WeakKeyDictionary()


def execution_lock():
    loop = asyncio.get_running_loop()
    return _locks.setdefault(loop, asyncio.Lock())


active_calls = set()
registered_calls = set()


@dataclass(eq=False)
class CallPermit:
    factory: object
    connection_id: int
    provider: str
    credential_revision: int
    owner_id: int
    operation: str
    role: str | None
    admin: bool
    timeouts: dict
    secret: str | None = field(repr=False)
    admitted_at: float
    task: asyncio.Task
    connection_version: int
    model_config_id: int | None = None
    config_version: int | None = None
    probe_id: int | None = None
    model_id: str | None = None
    capability_version: str | None = None
    contract_version: str | None = None

    def release_slot(self):
        active_calls.discard(self)

    def release(self):
        self.release_slot()
        registered_calls.discard(self)
        self.secret = None


def approve_call(
    factory, connection, setting, *, owner_id, operation, role=None, admin=True
):
    limits, timeouts = settings_values(setting)
    total = len(active_calls)
    provider = sum(c.provider == connection.provider for c in active_calls)
    administrators = sum(c.admin for c in active_calls)
    if (
        total >= limits["total"] - int(admin)
        or provider >= limits[connection.provider] - int(admin)
        or (admin and administrators >= limits["admin"])
    ):
        raise InvocationError("call_limit_reached")
    if not connection.enabled:
        raise InvocationError("configuration_unavailable")
    try:
        secret = decrypt_key(connection)
    except ProviderSecretUnavailableError:
        raise InvocationError("configuration_unavailable") from None
    permit = CallPermit(
        factory,
        connection.id,
        connection.provider,
        connection.credential_revision,
        owner_id,
        operation,
        role,
        admin,
        timeouts,
        secret,
        asyncio.get_running_loop().time(),
        asyncio.current_task(),
        connection.connection_version,
    )
    active_calls.add(permit)
    registered_calls.add(permit)
    return permit


async def admit_call(
    factory,
    *,
    connection_id,
    owner_id,
    operation,
    role=None,
    admin=True,
    expected_connection_version=None,
    model_config_id=None,
    expected_model_version=None,
    probe_id=None,
):
    async with execution_lock():
        permit = None
        try:
            async with factory() as db:
                connection = await db.get(ProviderConnection, connection_id)
                setting = await db.get(AppSetting, 1)
                if connection is None or setting is None:
                    raise InvocationError("configuration_unavailable")
                if (
                    expected_connection_version is not None
                    and connection.connection_version
                    != expected_connection_version
                ):
                    raise InvocationError("version_conflict")
                if probe_id is not None:
                    probe = await db.get(ModelProbe, probe_id)
                    if probe is None:
                        raise InvocationError("configuration_unavailable")
                    model = await db.get(ModelConfig, probe.model_config_id)
                    if model is None or not current_probe(
                        probe, model, connection
                    ):
                        raise InvocationError("configuration_unavailable")
                elif model_config_id is not None:
                    model = await db.get(ModelConfig, model_config_id)
                    if (
                        model is None
                        or model.provider_connection_id != connection_id
                        or model.config_version != expected_model_version
                        or not model_available(model, connection, role)
                    ):
                        raise InvocationError("configuration_unavailable")
                elif operation != "model_list":
                    raise InvocationError("configuration_unavailable")
                permit = approve_call(
                    factory,
                    connection,
                    setting,
                    owner_id=owner_id,
                    operation=operation,
                    role=role,
                    admin=admin,
                )
                permit.model_config_id = model_config_id
                permit.config_version = expected_model_version
                permit.probe_id = probe_id
                if model_config_id is not None:
                    permit.model_id = model.model_id
                    permit.capability_version = (
                        model.capability_definition_version
                    )
                    permit.contract_version = ROLE_CONTRACT_VERSIONS[role]
                elif probe_id is not None:
                    permit.model_id = model.model_id
            return permit
        except BaseException:
            if permit is not None:
                permit.release()
            raise


def cancel_connection(connection_id):
    for call in list(registered_calls):
        if call.connection_id == connection_id and not call.task.done():
            call.task.cancel()


def active_connection_count(provider):
    return sum(call.provider == provider for call in active_calls)


async def recheck_call(permit):
    async with execution_lock():
        async with permit.factory() as db:
            connection = await db.get(ProviderConnection, permit.connection_id)
            if (
                connection is None
                or not connection.enabled
                or not connection.encrypted_key
            ):
                raise InvocationError("configuration_unavailable")
            if permit.probe_id is not None:
                probe = await db.get(ModelProbe, permit.probe_id)
                if probe is None:
                    raise InvocationError("configuration_unavailable")
                model = await db.get(ModelConfig, probe.model_config_id)
                if model is None or not current_probe(probe, model, connection):
                    raise InvocationError("configuration_unavailable")
            elif permit.model_config_id is not None:
                model = await db.get(ModelConfig, permit.model_config_id)
                if model is None:
                    raise InvocationError("configuration_unavailable")
                definition = capabilities(permit.provider, model.model_id)
                if (
                    model.config_version != permit.config_version
                    or not model.enabled
                    or definition is None
                    or definition["definition_version"]
                    != permit.capability_version
                    or model.capability_definition_version
                    != permit.capability_version
                    or ROLE_CONTRACT_VERSIONS[permit.role]
                    != permit.contract_version
                    or metadata_conflict(model.model_id, connection)
                    or (
                        connection.connection_version
                        == permit.connection_version
                        and not model_available(model, connection, permit.role)
                    )
                ):
                    raise InvocationError("configuration_unavailable")


async def readmit_call(permit):
    # Backoff releases capacity but stays registered for connection revocation.
    current = await admit_call(
        permit.factory,
        connection_id=permit.connection_id,
        owner_id=permit.owner_id,
        operation=permit.operation,
        role=permit.role,
        admin=permit.admin,
        expected_connection_version=permit.connection_version,
        model_config_id=permit.model_config_id,
        expected_model_version=permit.config_version,
        probe_id=permit.probe_id,
    )
    current.timeouts = permit.timeouts
    current.admitted_at = permit.admitted_at
    permit.release()
    return current

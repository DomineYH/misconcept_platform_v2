"""Validate stored role evidence against current connection/definition versions."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from src.models.model_config import ROLES
from src.services.model_capabilities import (
    capabilities,
    metadata_conflict,
    validate_model_and_options,
)
from src.services.provider_secrets import (
    ProviderSecretUnavailableError,
    decrypt_key,
)

ROLE_CONTRACT_VERSIONS = {role: "s1-v1" for role in ROLES}
ROLE_CONTRACT_VERSIONS["mentor"] = "s3-v1"
ROLE_CONTRACT_VERSIONS["analysis"] = "s4-v2"
DEMO_SEED_TIME = datetime(2026, 10, 18, tzinfo=timezone.utc)


class RoleVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["unverified", "verifying", "succeeded", "failed", "stale"]
    verified_at: datetime | None = None
    credential_revision: StrictInt | None = Field(default=None, ge=0)
    connection_version: StrictInt | None = Field(default=None, ge=1)
    capability_definition_version: str | None = None
    role_contract_version: str | None = None
    probe_request_id: str | None = None
    error_code: (
        Literal[
            "interrupted",
            "invalid_output",
            "invalid_json",
            "invalid_reference",
            "configuration_unavailable",
            "call_limit_reached",
            "authentication",
            "permission",
            "model_unavailable",
            "quota",
            "rate_limited",
            "transient",
            "timeout_connect",
            "timeout_first_output",
            "timeout_total",
            "refused",
            "output_limit",
            "context_limit",
            "empty_response",
        ]
        | None
    ) = None


def connection_ready(connection, *, app_mode):
    if (connection.provider == "demo") != (
        app_mode == "demo"
    ) or not connection.enabled:
        return False
    if connection.provider == "demo":
        # Keyless; marker/manifest were verified before this visitor DB opened.
        return True
    try:
        decrypt_key(connection)
        return True
    except ProviderSecretUnavailableError:
        return False


def effective_roles(model, connection, *, app_mode):
    definition = capabilities(connection.provider, model.model_id)
    ready = connection_ready(connection, app_mode=app_mode)
    demo = connection.provider == "demo"
    states = {}
    for role in ROLES:
        try:
            state = RoleVerification.model_validate(
                model.verification_state.get(role, {})
            )
        except (ValidationError, AttributeError):
            states[role] = {"status": "unverified"}
            continue
        if state.status != "unverified" and (
            not ready
            or definition is None
            or model.capability_definition_version
            != definition["definition_version"]
            or state.credential_revision != connection.credential_revision
            or state.connection_version != connection.connection_version
            or state.capability_definition_version
            != definition["definition_version"]
            or state.role_contract_version != ROLE_CONTRACT_VERSIONS[role]
            or demo
            and (
                state.verified_at != DEMO_SEED_TIME
                or state.credential_revision != 0
                or state.connection_version != 1
                or state.probe_request_id is not None
            )
        ):
            state.status = "stale"
        states[role] = state.model_dump(exclude_none=True)
        if demo and ready and state.status == "stale":
            states[role]["error_code"] = "VERSION_MISMATCH"
    return states


def model_available(model, connection, role, options=None, *, app_mode):
    if (
        not model.enabled
        or metadata_conflict(model.model_id, connection)
        or effective_roles(model, connection, app_mode=app_mode)[role]["status"]
        != "succeeded"
    ):
        return False
    try:
        validate_model_and_options(
            connection.provider,
            model.model_id,
            model.default_options_json if options is None else options,
            app_mode=app_mode,
        )
        return True
    except ValueError:
        return False

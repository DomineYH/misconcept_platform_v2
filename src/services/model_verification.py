"""Validate stored role evidence against current connection/definition versions."""

from datetime import datetime
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


def connection_ready(connection):
    if not connection.enabled:
        return False
    try:
        decrypt_key(connection)
        return True
    except ProviderSecretUnavailableError:
        return False


def effective_roles(model, connection):
    definition = capabilities(connection.provider, model.model_id)
    ready = connection_ready(connection)
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
        ):
            state.status = "stale"
        states[role] = state.model_dump(exclude_none=True)
    return states


def model_available(model, connection, role, options=None):
    if (
        not model.enabled
        or metadata_conflict(model.model_id, connection)
        or effective_roles(model, connection)[role]["status"] != "succeeded"
    ):
        return False
    try:
        validate_model_and_options(
            connection.provider,
            model.model_id,
            model.default_options_json if options is None else options,
        )
        return True
    except ValueError:
        return False

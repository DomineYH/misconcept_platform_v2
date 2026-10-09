"""Conditional role/probe finalization against current version evidence."""

import json

from sqlalchemy import func, select, text, update

from src.models import ApiUsageLog, ModelConfig, ModelProbe, ProviderConnection
from src.models.provider_connection import now
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_verification import (
    ROLE_CONTRACT_VERSIONS,
    connection_ready,
)


def probe_evidence(probe, status, error_code=None):
    return dict(
        status=status,
        verified_at=now().isoformat() if status != "verifying" else None,
        credential_revision=probe.credential_revision,
        connection_version=probe.connection_version,
        capability_definition_version=probe.capability_definition_version,
        role_contract_version=probe.role_contract_version,
        probe_request_id=probe.request_id,
        error_code=error_code,
    )


def current_probe(probe, model, connection):
    definition = capabilities(connection.provider, model.model_id)
    return bool(
        probe.status == "verifying"
        and definition
        and model.config_version == probe.config_version
        and connection.credential_revision == probe.credential_revision
        and connection.connection_version == probe.connection_version
        and definition["definition_version"]
        == probe.capability_definition_version
        and model.capability_definition_version
        == probe.capability_definition_version
        and ROLE_CONTRACT_VERSIONS[probe.role] == probe.role_contract_version
        and not metadata_conflict(model.model_id, connection)
        and connection_ready(connection)
    )


async def finish_probe(factory, probe_id, status, error_code=None):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        probe = await db.get(ModelProbe, probe_id)
        if probe is None or probe.status != "verifying":
            return
        model = await db.get(ModelConfig, probe.model_config_id)
        connection = await db.get(
            ProviderConnection, model.provider_connection_id
        )
        if not current_probe(probe, model, connection):
            status = "stale"
        probe.status, probe.error_code, probe.finished_at = (
            status,
            error_code,
            now(),
        )
        await db.execute(
            update(ModelConfig)
            .where(
                ModelConfig.id == model.id,
                func.json_extract(
                    ModelConfig.verification_state,
                    f"$.{probe.role}.probe_request_id",
                )
                == probe.request_id,
            )
            .values(
                verification_state=func.json_set(
                    ModelConfig.verification_state,
                    f"$.{probe.role}",
                    func.json(
                        json.dumps(probe_evidence(probe, status, error_code))
                    ),
                )
            )
        )
        await db.commit()


async def interrupt_probe_orphans(factory):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        probes = (
            await db.scalars(
                select(ModelProbe).where(ModelProbe.status == "verifying")
            )
        ).all()
        for probe in probes:
            probe.status, probe.error_code, probe.finished_at = (
                "failed",
                "interrupted",
                now(),
            )
            await db.execute(
                update(ModelConfig)
                .where(
                    ModelConfig.id == probe.model_config_id,
                    func.json_extract(
                        ModelConfig.verification_state,
                        f"$.{probe.role}.probe_request_id",
                    )
                    == probe.request_id,
                )
                .values(
                    verification_state=func.json_set(
                        ModelConfig.verification_state,
                        f"$.{probe.role}",
                        func.json(
                            json.dumps(
                                probe_evidence(probe, "failed", "interrupted")
                            )
                        ),
                    )
                )
            )
        await db.execute(
            update(ApiUsageLog)
            .where(
                ApiUsageLog.invocation_id.is_not(None),
                ApiUsageLog.status == "running",
                ApiUsageLog.finished_at.is_(None),
            )
            .values(
                status="interrupted",
                error_code="interrupted",
                finished_at=now(),
                usage_complete=False,
            )
        )
        await db.commit()


async def cancel_reserved_probe(factory, probe_id):
    await finish_probe(factory, probe_id, "failed", "interrupted")
    async with factory() as db:
        probe = await db.get(ModelProbe, probe_id)
        if probe is None:
            raise InvocationError("configuration_unavailable")
        await db.execute(
            update(ApiUsageLog)
            .where(
                ApiUsageLog.owner_id == probe.owner_id,
                ApiUsageLog.request_id == probe.request_id,
                ApiUsageLog.status == "running",
                ApiUsageLog.finished_at.is_(None),
            )
            .values(
                status="cancelled",
                error_code="interrupted",
                finished_at=now(),
                usage_complete=False,
            )
        )
        await db.commit()

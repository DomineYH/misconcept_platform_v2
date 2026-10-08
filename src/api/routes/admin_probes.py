"""Explicit durable student-role probe reservations and safe status reads."""

import asyncio
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_providers import CredentialRoute
from src.models import (
    AppSetting,
    ModelConfig,
    ModelProbe,
    ProviderConnection,
    User,
)
from src.models.provider_connection import now
from src.services.model_capabilities import capabilities, metadata_conflict
from src.services.model_configuration import settings_values
from src.services.model_verification import (
    ROLE_CONTRACT_VERSIONS,
    connection_ready,
)
from src.services.probe_lifecycle import probe_evidence
from src.services.student_probe_contract import probe_options

router = APIRouter(tags=["Admin Probes"], route_class=CredentialRoute)


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_version: StrictInt = Field(gt=0)
    role: Literal["student"]
    request_id: UUID


def public_probe(probe):
    return dict(
        request_id=probe.request_id,
        status=probe.status,
        error_code=probe.error_code,
    )


@router.post("/admin/ai/models/{model_id}/probes", status_code=202)
async def reserve_probe(
    model_id: int,
    data: ProbeRequest,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    owner_id = user.id
    fingerprint = hashlib.sha256(
        json.dumps(
            dict(
                model_id=model_id,
                role=data.role,
                expected_version=data.expected_version,
            ),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    prior = await db.scalar(
        select(ModelProbe).where(
            ModelProbe.owner_id == owner_id,
            ModelProbe.request_id == str(data.request_id),
        )
    )
    if prior:
        if prior.fingerprint != fingerprint:
            raise HTTPException(409, detail={"code": "request_conflict"})
        return public_probe(prior)
    model = await db.get(ModelConfig, model_id)
    if model is None:
        raise HTTPException(404, detail={"code": "model_not_found"})
    if model.config_version != data.expected_version:
        raise HTTPException(409, detail={"code": "version_conflict"})
    connection = await db.get(ProviderConnection, model.provider_connection_id)
    definition = capabilities(connection.provider, model.model_id)
    if (
        connection.provider != "openai"
        or definition is None
        or metadata_conflict(model.model_id, connection)
    ):
        raise HTTPException(
            422, detail={"code": "capability_definition_required"}
        )
    if not connection_ready(connection):
        raise HTTPException(503, detail={"code": "configuration_unavailable"})
    try:
        options = probe_options(connection.provider, model)
    except ValueError:
        raise HTTPException(422, detail={"code": "invalid_options"}) from None
    setting = await db.get(AppSetting, 1)
    if setting is None:
        raise HTTPException(503, detail={"code": "configuration_unavailable"})
    settings_values(setting)
    probe = ModelProbe(
        owner_id=owner_id,
        model_config_id=model_id,
        role=data.role,
        request_id=str(data.request_id),
        fingerprint=fingerprint,
        credential_revision=connection.credential_revision,
        connection_version=connection.connection_version,
        config_version=model.config_version,
        capability_definition_version=definition["definition_version"],
        role_contract_version=ROLE_CONTRACT_VERSIONS[data.role],
        options_json=options,
        status="verifying",
        started_at=now(),
    )
    db.add(probe)
    try:
        await db.flush()
        await db.execute(
            update(ModelConfig)
            .where(ModelConfig.id == model_id)
            .values(
                verification_state=func.json_set(
                    ModelConfig.verification_state,
                    "$.student",
                    func.json(json.dumps(probe_evidence(probe, "verifying"))),
                ),
                capabilities_json=definition,
                capability_definition_version=definition["definition_version"],
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail={"code": "probe_in_progress"}) from None
    from src.services.probe_execution import start_probe

    start_probe(async_sessionmaker(db.bind, expire_on_commit=False), probe.id)
    return public_probe(probe)


@router.get("/admin/ai/probes/{request_id}")
async def probe_status(
    request_id: UUID,
    response: Response,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    probe = await db.scalar(
        select(ModelProbe).where(
            ModelProbe.owner_id == user.id,
            ModelProbe.request_id == str(request_id),
        )
    )
    if probe is None:
        raise HTTPException(404, detail={"code": "probe_not_found"})
    response.headers["Cache-Control"] = "no-store"
    return public_probe(probe)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


@router.post("/admin/ai/probes/{request_id}/cancel")
async def cancel_probe(
    request_id: UUID,
    data: CancelRequest,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.probe_execution import active_probes
    from src.services.probe_lifecycle import cancel_reserved_probe

    probe = await db.scalar(
        select(ModelProbe).where(
            ModelProbe.owner_id == user.id,
            ModelProbe.request_id == str(request_id),
        )
    )
    if probe is None:
        raise HTTPException(404, detail={"code": "probe_not_found"})
    probe_id = probe.id
    pending = probe.status == "verifying"
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    await db.rollback()
    if pending:
        task = active_probes.get(probe_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cancel_reserved_probe(factory, probe_id)
    return {"status": "cancel_requested"}

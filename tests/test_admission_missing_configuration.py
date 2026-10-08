"""Missing stored configuration fails closed at public service boundaries."""

import asyncio

import httpx2
import pytest
from sqlalchemy import text
from test_model_management import write
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import prepare, response_body, sdk_transport

from src.services.call_admission import admit_call, readmit_call, recheck_call
from src.services.invocation_types import InvocationError
from src.services.probe_lifecycle import cancel_reserved_probe

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def test_missing_probe_admission_and_cancel_are_safe(data, api):
    await prepare(api)
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await admit_call(
            data.factory,
            connection_id=1,
            owner_id=data.admin.id,
            operation="probe",
            role="student",
            probe_id=999,
        )
    with pytest.raises(InvocationError, match="configuration_unavailable"):
        await cancel_reserved_probe(data.factory, 999)


async def test_readmission_keeps_probe_binding_and_checks_missing_row(
    data, api, monkeypatch
):
    body = await prepare(api)
    opened, release = asyncio.Event(), asyncio.Event()

    async def upstream(request, payload):
        opened.set()
        await release.wait()
        return httpx2.Response(200, json=response_body())

    sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    async with asyncio.timeout(5):
        await opened.wait()
    permit = None
    try:
        async with data.engine.connect() as db:
            probe_id = (
                await db.execute(text("SELECT id FROM model_probe"))
            ).scalar_one()
        permit = await admit_call(
            data.factory,
            connection_id=1,
            owner_id=data.admin.id,
            operation="probe",
            role="student",
            probe_id=probe_id,
        )
        permit.release_slot()
        original_time = permit.admitted_at
        permit = await readmit_call(permit)
        assert (
            permit.probe_id == probe_id and permit.admitted_at == original_time
        )
        await recheck_call(permit)
        async with data.engine.begin() as db:
            await db.execute(text("DELETE FROM model_probe"))
        with pytest.raises(InvocationError, match="configuration_unavailable"):
            await recheck_call(permit)
    finally:
        if permit:
            permit.release()
        release.set()

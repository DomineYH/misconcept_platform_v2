"""Page-independent, explicitly reserved probe tasks for the single worker."""

import asyncio
import logging
from contextlib import aclosing

from src.models import ModelConfig, ModelProbe
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError, TextRequest
from src.services.probe_lifecycle import finish_probe
from src.services.student_probe_contract import MESSAGES, SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)
active_probes: dict[int, asyncio.Task] = {}
cleanup_tasks: set[asyncio.Task] = set()


async def run_probe(factory, probe_id, permit):
    try:
        async with factory() as db:
            probe = await db.get(ModelProbe, probe_id)
            model = await db.get(ModelConfig, probe.model_config_id)
            connection_id = model.provider_connection_id
            request = TextRequest(
                permit.provider,
                model.model_id,
                probe.role,
                SYSTEM_INSTRUCTION,
                MESSAGES,
                probe.options_json,
                probe.request_id,
            )
        for step in ("text", "stream"):
            if step == "stream":
                permit = await admit_call(
                    factory,
                    connection_id=connection_id,
                    owner_id=probe.owner_id,
                    operation="probe",
                    role=request.role,
                    probe_id=probe_id,
                )
            async with aclosing(
                execute_call(permit, request, kind=step, probe_step=step)
            ) as events:
                async for terminal in events:
                    pass
            if terminal.type != "completed":
                raise InvocationError(terminal.error_code or "invalid_output")
        await finish_probe(factory, probe_id, "succeeded")
    except asyncio.CancelledError:
        await cleanup_failure(factory, probe_id, "interrupted")
    except Exception as error:
        code = (
            error.code
            if isinstance(error, InvocationError)
            else "configuration_unavailable"
        )
        await cleanup_failure(factory, probe_id, code)
    finally:
        permit.release()


def start_probe(factory, probe_id, permit):
    task = asyncio.create_task(run_probe(factory, probe_id, permit))
    permit.task = task
    active_probes[probe_id] = task

    def done(task):
        active_probes.pop(probe_id, None)
        permit.release()
        if task.cancelled():
            from src.services.probe_lifecycle import cancel_reserved_probe

            cleanup = asyncio.create_task(
                cancel_reserved_probe(factory, probe_id)
            )
            cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(cleanup_done)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Probe state cleanup failed")

    task.add_done_callback(done)


def cleanup_done(task):
    cleanup_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Probe state cleanup failed")


async def cleanup_failure(factory, probe_id, code):
    try:
        await finish_probe(factory, probe_id, "failed", code)
    except Exception:
        logger.error("Probe state cleanup failed")


async def stop_probes():
    tasks = list(active_probes.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)

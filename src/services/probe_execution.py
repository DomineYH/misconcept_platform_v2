"""Page-independent, explicitly reserved probe tasks for the single worker."""

import asyncio
import logging
from contextlib import aclosing
from dataclasses import replace

from src.demo.lifecycle import (
    NORMAL_SCOPE,
    NormalContext,
    TaskControl,
    lifecycle,
    scope_for,
)
from src.models import ModelConfig, ModelProbe
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError
from src.services.probe_lifecycle import finish_probe
from src.services.role_probe_contract import probe_steps

logger = logging.getLogger(__name__)
active_probes = lifecycle.normal_view("probe", lambda task: task.cancel())
cleanup_tasks: set[asyncio.Task] = set()


async def run_probe(factory, probe_id, permit, *, ctx=None, registry=lifecycle):
    ctx = ctx if ctx is not None else NormalContext(factory)
    factory = ctx.factory
    try:
        await registry.assert_current(ctx)
        async with factory() as db:
            probe = await db.get(ModelProbe, probe_id)
            model = await db.get(ModelConfig, probe.model_config_id)
            connection_id = model.provider_connection_id
            steps = probe_steps(
                permit.provider,
                model.model_id,
                probe.role,
                probe.options_json,
                probe.request_id,
            )
        for index, (step, kind, request) in enumerate(steps):
            if index:
                permit = await admit_call(
                    factory,
                    connection_id=connection_id,
                    owner_id=probe.owner_id,
                    operation="probe",
                    role=request.role,
                    probe_id=probe_id,
                )
            async with aclosing(
                execute_call(permit, request, kind=kind, probe_step=step)
            ) as events:
                async for terminal in events:
                    pass
            if terminal.type != "completed":
                raise InvocationError(terminal.error_code or "invalid_output")
        await finish_probe(
            factory, probe_id, "succeeded", ctx=ctx, registry=registry
        )
    except asyncio.CancelledError:
        await cleanup_failure(
            factory, probe_id, "interrupted", ctx=ctx, registry=registry
        )
    except Exception as error:
        code = (
            error.code
            if isinstance(error, InvocationError)
            else "configuration_unavailable"
        )
        await cleanup_failure(
            factory, probe_id, code, ctx=ctx, registry=registry
        )
    finally:
        permit.release()


def start_probe(
    factory, probe_id, permit, *, ctx=None, registry=lifecycle, token=None
):
    ctx = ctx if ctx is not None else NormalContext(factory)
    factory = ctx.factory
    if scope_for(ctx) != NORMAL_SCOPE and token is None:
        raise ValueError("Demo probe requires scoped registration")
    task = asyncio.create_task(
        run_probe(factory, probe_id, permit, ctx=ctx, registry=registry)
    )
    permit.task = task
    if token is None:
        active_probes[probe_id] = task
        token = lifecycle.lookup(NORMAL_SCOPE, "probe", probe_id)
        registry = lifecycle

    async def finalize(completed):
        try:
            if completed.cancelled():
                from src.services.probe_lifecycle import cancel_reserved_probe

                await cancel_reserved_probe(
                    factory, probe_id, ctx=ctx, registry=registry
                )
            elif completed.exception() is not None:
                logger.error("Probe state cleanup failed")
        finally:
            permit.release()
            token.task_control.done.set()
            await registry.pop(*token.key, token)

    def done(completed):
        cleanup = asyncio.create_task(finalize(completed))
        cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(cleanup_done)

    task.add_done_callback(done)
    return task


async def start_scoped_probe(ctx, probe_id, permit, *, registry=lifecycle):
    tasks = []
    token = None
    try:
        token = await registry.register(
            ctx,
            "probe",
            probe_id,
            TaskControl(lambda: tasks[0].cancel(), asyncio.Event()),
        )
        child = replace(ctx, lease=token.registry_lease)
        tasks.append(
            start_probe(
                ctx.factory,
                probe_id,
                permit,
                ctx=child,
                registry=registry,
                token=token,
            )
        )
        return token
    except BaseException:
        permit.release()
        if token is not None:
            token.task_control.done.set()
            await registry.pop(*token.key, token)
        raise


def cleanup_done(task):
    cleanup_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Probe state cleanup failed")


async def cleanup_failure(
    factory, probe_id, code, *, ctx=None, registry=lifecycle
):
    try:
        await finish_probe(
            factory, probe_id, "failed", code, ctx=ctx, registry=registry
        )
    except Exception:
        logger.error("Probe state cleanup failed")


async def stop_probes():
    tasks = list(active_probes.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    while cleanup_tasks:
        pending = tuple(cleanup_tasks)
        await asyncio.gather(*pending, return_exceptions=True)
        cleanup_tasks.difference_update(pending)

"""Page-independent, explicitly reserved probe tasks for the single worker."""

import asyncio
import logging
from contextlib import aclosing

from src.models import AppSetting, ModelConfig, ModelProbe, ProviderConnection
from src.models.provider_connection import now
from src.services import openai_generation
from src.services.invocation_ledger import finish_attempt, start_attempt
from src.services.invocation_types import InvocationError, TextRequest
from src.services.model_configuration import settings_values
from src.services.probe_lifecycle import current_probe, finish_probe
from src.services.provider_secrets import decrypt_key
from src.services.student_probe_contract import MESSAGES, SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)
active_probes: dict[int, asyncio.Task] = {}


async def run_probe(factory, probe_id):
    entry_id = None
    usage = first_output_at = None
    try:
        async with factory() as db:
            probe = await db.get(ModelProbe, probe_id)
            model = await db.get(ModelConfig, probe.model_config_id)
            connection = await db.get(
                ProviderConnection, model.provider_connection_id
            )
            if not current_probe(probe, model, connection):
                raise InvocationError("configuration_unavailable")
            secret = decrypt_key(connection)
            request = TextRequest(
                connection.provider,
                model.model_id,
                probe.role,
                SYSTEM_INSTRUCTION,
                MESSAGES,
                probe.options_json,
                probe.request_id,
            )
        for step in ("text", "stream"):
            async with factory() as db:
                current = await db.get(ModelProbe, probe_id)
                model = await db.get(ModelConfig, current.model_config_id)
                connection = await db.get(
                    ProviderConnection, model.provider_connection_id
                )
                if not current_probe(current, model, connection):
                    raise InvocationError("configuration_unavailable")
                setting = await db.get(AppSetting, 1)
                if setting is None:
                    raise InvocationError("configuration_unavailable")
                _, timeouts = settings_values(setting)
            entry_id = await start_attempt(
                factory,
                request_id=probe.request_id,
                owner_id=probe.owner_id,
                provider=request.provider,
                model=request.model_id,
                role=request.role,
                operation="probe",
                credential_revision=probe.credential_revision,
                probe_step=step,
            )
            usage = first_output_at = None
            if step == "text":
                terminal = await openai_generation.generate_text(
                    request, secret, timeouts
                )
                usage = terminal.usage
                if terminal.text.strip():
                    first_output_at = now()
            else:
                async with aclosing(
                    openai_generation.stream_text(request, secret, timeouts)
                ) as events:
                    async for event in events:
                        if (
                            event.type == "text_delta"
                            and event.text.strip()
                            and first_output_at is None
                        ):
                            first_output_at = now()
                        if event.usage is not None:
                            usage = event.usage
                        if event.type in (
                            "completed",
                            "refused",
                            "interrupted",
                            "error",
                        ):
                            terminal = event
                            break
            code = terminal.error_code
            if not await finish_attempt(
                factory,
                entry_id,
                status=ledger_status(terminal),
                error_code=code,
                usage=usage,
                first_output_at=first_output_at,
            ):
                raise InvocationError("interrupted")
            entry_id = None
            if terminal.type != "completed":
                raise InvocationError(code or "invalid_output")
        await finish_probe(factory, probe_id, "succeeded")
    except asyncio.CancelledError:
        await cleanup_failure(
            factory, probe_id, entry_id, "interrupted", usage, first_output_at
        )
    except Exception as error:
        code = (
            error.code
            if isinstance(error, InvocationError)
            else "configuration_unavailable"
        )
        await cleanup_failure(
            factory, probe_id, entry_id, code, usage, first_output_at
        )

    finally:
        secret = None


def start_probe(factory, probe_id):
    task = asyncio.create_task(run_probe(factory, probe_id))
    active_probes[probe_id] = task

    def done(task):
        active_probes.pop(probe_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Probe state cleanup failed")

    task.add_done_callback(done)


def ledger_status(event):
    if event.type == "completed":
        return "completed"
    if event.type == "refused":
        return "refused"
    if event.type == "interrupted":
        return "cancelled"
    return (
        "timed_out"
        if (event.error_code or "").startswith("timeout_")
        else "failed"
    )


async def cleanup_failure(
    factory, probe_id, entry_id, code, usage, first_output_at
):
    if entry_id is not None:
        try:
            await finish_attempt(
                factory,
                entry_id,
                status="cancelled" if code == "interrupted" else "failed",
                error_code=code,
                usage=usage,
                first_output_at=first_output_at,
            )
        except InvocationError:
            pass
    try:
        await finish_probe(factory, probe_id, "failed", code)
    except Exception:
        logger.error("Probe state cleanup failed")


async def stop_probes():
    tasks = list(active_probes.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

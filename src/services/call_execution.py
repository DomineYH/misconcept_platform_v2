"""One attempt/deadline/retry/cleanup boundary for catalog and generation."""

import asyncio
import json
from contextlib import aclosing
from uuid import uuid4

from fastapi import HTTPException

from src.models.provider_connection import now
from src.services import (
    anthropic_catalog,
    anthropic_generation,
    google_catalog,
    google_generation,
    openai_catalog,
    openai_generation,
)
from src.services.call_admission import (
    readmit_call,
    recheck_call,
    registered_calls,
)
from src.services.call_policy import CallDeadline, retry_limit
from src.services.invocation_ledger import finish_attempt, start_attempt
from src.services.invocation_types import CallEvent, InvocationError
from src.services.lesson_snapshots import load_active_lesson

PROVIDER_ADAPTERS = {
    "openai": (openai_catalog, openai_generation),
    "anthropic": (anthropic_catalog, anthropic_generation),
    "google": (google_catalog, google_generation),
}


async def sdk_events(permit, request, kind, deadline):
    catalog, generation = PROVIDER_ADAPTERS[permit.provider]
    if kind == "catalog":
        try:
            models = await catalog.list_models(
                permit.secret,
                connect_timeout=permit.timeouts["connect"],
                total_timeout=permit.timeouts["model_list_total"],
                deadline=deadline,
            )
            yield CallEvent("completed", models=models)
        except (openai_catalog.CatalogError, InvocationError) as error:
            yield CallEvent("error", error_code=error.code)
    elif kind == "stream":
        async with aclosing(
            generation.stream_text(
                request, permit.secret, permit.timeouts, deadline=deadline
            )
        ) as events:
            async for event in events:
                yield event
    elif kind == "structured":
        yield await generation.generate_structured(
            request, permit.secret, permit.timeouts, deadline=deadline
        )
    else:
        yield await generation.generate_text(
            request, permit.secret, permit.timeouts, deadline=deadline
        )


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


async def finalize(permit, entry_id, terminal, usage, first_output_at):
    task = asyncio.create_task(
        finish_attempt(
            permit.factory,
            entry_id,
            status=ledger_status(terminal),
            error_code=terminal.error_code,
            usage=usage,
            first_output_at=first_output_at,
        )
    )
    try:
        changed = await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    if not changed:
        raise InvocationError("interrupted")


async def execute_call(
    permit,
    request=None,
    *,
    kind="text",
    probe_step=None,
    run_id=None,
    session_id=None,
):
    """Consume with aclosing; admission is never a queued or reusable token."""
    entry_id = None
    usage = first_output_at = terminal = None
    deadline = CallDeadline(
        permit.timeouts, permit.role, admitted_at=permit.admitted_at
    )
    invocation_id = str(uuid4())
    wait_ms = None
    try:
        if (
            permit.provider not in PROVIDER_ADAPTERS
            or permit not in registered_calls
            or permit.task is not asyncio.current_task()
        ):
            raise InvocationError("configuration_unavailable")
        if (
            kind not in ("text", "stream", "catalog", "structured")
            or (kind == "catalog") != (permit.operation == "model_list")
            or (kind != "catalog" and request is None)
        ):
            raise InvocationError("configuration_unavailable")
        if request is not None and (
            request.model_id != permit.model_id
            or request.provider != permit.provider
            or request.role != permit.role
        ):
            raise InvocationError("configuration_unavailable")
        for attempt in range(
            1, retry_limit(permit.operation, permit.role, permit.admin) + 2
        ):
            terminal = None
            if deadline.remaining() <= 0:
                raise InvocationError("timeout_total")
            usage = first_output_at = None
            starting = asyncio.create_task(
                start_attempt(
                    permit.factory,
                    request_id=request.request_id if request else str(uuid4()),
                    owner_id=permit.owner_id,
                    provider=permit.provider,
                    model=request.model_id if request else None,
                    role=permit.role,
                    operation=permit.operation,
                    credential_revision=permit.credential_revision,
                    probe_step=probe_step,
                    invocation_id=invocation_id,
                    attempt_no=attempt,
                    retry_wait_ms=wait_ms,
                    run_id=run_id,
                    session_id=session_id,
                    context_budget_json=(
                        request.context_budget_json if request else None
                    ),
                )
            )
            try:
                entry_id = await asyncio.shield(starting)
            except asyncio.CancelledError:
                entry_id = await starting
                raise
            await recheck_call(permit)
            if permit.operation in ("student", "mentor") and not permit.admin:
                async with permit.factory() as db:
                    try:
                        await load_active_lesson(
                            db, session_id, permit.owner_id
                        )
                    except HTTPException:
                        raise InvocationError(
                            "configuration_unavailable"
                        ) from None
            if (
                permit.role == "analysis"
                and not permit.admin
                and session_id is not None
            ):
                from src.services.analysis_pipeline import load_analysis_lesson

                async with permit.factory() as db:
                    try:
                        if run_id is not None:
                            from src.models import GenerationRun, User

                            run = await db.get(GenerationRun, run_id)
                            if run is None or run.status != "running":
                                raise InvocationError("interrupted")
                            if json.loads(run.plan_json).get("regenerate"):
                                actor = await db.get(
                                    User, request.validation_context["actor_id"]
                                )
                                if actor is None or not actor.is_admin:
                                    raise InvocationError(
                                        "configuration_unavailable"
                                    )
                        await load_analysis_lesson(
                            db,
                            session_id,
                            request.validation_context["actor_id"],
                        )
                    except HTTPException:
                        raise InvocationError(
                            "configuration_unavailable"
                        ) from None
            if deadline.remaining() <= 0:
                raise InvocationError("timeout_total")
            async with deadline.total():
                async with aclosing(
                    sdk_events(permit, request, kind, deadline)
                ) as events:
                    async for event in events:
                        if event.usage is not None:
                            usage = event.usage
                        if event.text.strip() and first_output_at is None:
                            first_output_at = now()
                        if event.type in ("text_delta", "usage"):
                            yield event
                        else:
                            terminal = event
                            break
            terminal = terminal or CallEvent(
                "error", error_code="invalid_output"
            )
            await finalize(permit, entry_id, terminal, usage, first_output_at)
            entry_id = None
            wait = (
                1.0
                if terminal.retry_after_seconds is None
                else terminal.retry_after_seconds
            )
            if (
                attempt
                <= retry_limit(permit.operation, permit.role, permit.admin)
                and terminal.error_code
                in ("transient", "rate_limited", "timeout_connect")
                and first_output_at is None
                and not terminal.response_received
                and wait < deadline.remaining()
            ):
                permit.release_slot()
                terminal = None
                async with deadline.total():
                    await asyncio.sleep(wait)
                permit = await readmit_call(permit)
                wait_ms = round(wait * 1000)
                continue
            permit.release()
            yield terminal
            return
    except asyncio.CancelledError:
        terminal = terminal or CallEvent(
            "interrupted", usage=usage, error_code="interrupted"
        )
    except TimeoutError:
        terminal = terminal or CallEvent(
            "error", usage=usage, error_code="timeout_total"
        )
    except InvocationError as error:
        code = (
            "configuration_unavailable"
            if error.code == "version_conflict"
            else error.code
        )
        terminal = CallEvent(
            "interrupted" if code == "interrupted" else "error",
            usage=usage,
            error_code=code,
        )
    except Exception:
        terminal = CallEvent(
            "error", usage=usage, error_code="configuration_unavailable"
        )
    finally:
        try:
            if entry_id is not None:
                terminal = terminal or CallEvent(
                    "interrupted", usage=usage, error_code="interrupted"
                )
                try:
                    await finalize(
                        permit, entry_id, terminal, usage, first_output_at
                    )
                except InvocationError as error:
                    terminal = CallEvent(
                        (
                            "interrupted"
                            if error.code == "interrupted"
                            else "error"
                        ),
                        usage=usage,
                        error_code=error.code,
                    )
        finally:
            permit.release()
    yield terminal

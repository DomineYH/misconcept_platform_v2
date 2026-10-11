"""Atomic comparison admission and durable results, without lesson writes."""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text, update

from src.api.schemas.comparison import ComparisonRequest, ComparisonRetry
from src.models import (
    ApiUsageLog,
    AppSetting,
    ComparisonRun,
    GenerationRun,
    Scenario,
    User,
)
from src.services.call_admission import approve_call, execution_lock
from src.services.comparison_preparation import (
    COMPARISON_ROLES,
    prepare_comparison,
    raise_input_error,
)
from src.services.comparison_state import (
    COMPLETED_STATUSES,
    comparison_busy,
    comparison_snapshot,
    draft_title,
    find_panel,
    owned_comparison,
    utcnow,
)
from src.services.generation_runs import conflict, digest
from src.services.invocation_types import InvocationError
from src.services.model_verification import ROLE_CONTRACT_VERSIONS


@dataclass(frozen=True)
class Admission:
    body: ComparisonRequest | ComparisonRetry
    input_hash: str
    comparison: ComparisonRun | None = None
    previous: GenerationRun | None = None
    frozen: dict | None = None
    replay: dict | None = None


async def reserve_comparison(
    factory, user, body, *, comparison_id=None, panel_id=None
):
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            admission = await admit_comparison(
                db, user, body, comparison_id, panel_id
            )
            if admission.replay is not None:
                return admission.replay, None
            history, prepared = await prepare_comparison(db, admission.body)
            executions, frozen_panels = await allocate_panels(
                factory, db, user, admission.body, prepared, admission.frozen
            )
            accepted = await persist_comparison(
                db, user, admission, history, executions, frozen_panels
            )
            return accepted, executions


async def admit_comparison(db, user, body, comparison_id, panel_id):
    actor = await db.get(User, user.id)
    if actor is None or not actor.is_admin:
        raise HTTPException(403, detail={"code": "admin_required"})
    input_hash = digest(body.model_dump(by_alias=True, mode="json"))
    comparison = previous = frozen = None
    if comparison_id is not None:
        comparison = await owned_comparison(db, comparison_id, user.id)
        snapshot = await comparison_snapshot(db, comparison)
        frozen = find_panel(snapshot, panel_id)
        replay = await db.scalar(
            select(GenerationRun).where(
                GenerationRun.comparison_run_id == comparison.id,
                GenerationRun.panel_id == panel_id,
                GenerationRun.request_id == str(body.request_id),
            )
        )
        if replay:
            if replay.input_hash != input_hash:
                conflict("request_conflict")
            return Admission(body, input_hash, replay=snapshot)
        if frozen["run_id"] != str(body.generation_id):
            conflict("stale_generation")
        if comparison_busy(snapshot):
            conflict("comparison_busy", comparison_run_id=comparison.id)
        if frozen["status"] not in (
            "failed",
            "cancelled",
            "interrupted",
            "degraded",
        ):
            conflict("new_comparison_required")
        previous = await db.get(GenerationRun, frozen["run_id"])
        body = ComparisonRequest.model_validate(
            dict(
                request_id=str(body.request_id),
                target_role=comparison.target_role,
                source_snapshot=comparison.source_snapshot,
                canonical_context=[
                    {"role": item["role"], "content": item["content"]}
                    for item in comparison.canonical_context
                ],
                current_message=comparison.current_message,
                panels=[
                    dict(
                        panel_id=panel_id,
                        model_config=frozen["model_config"],
                    )
                ],
            )
        )
    existing = (
        None
        if comparison is not None
        else await db.scalar(
            select(ComparisonRun).where(
                ComparisonRun.admin_id == user.id,
                ComparisonRun.request_id == body.request_id,
            )
        )
    )
    if existing:
        await owned_comparison(db, existing.id, user.id)
        if existing.input_hash != input_hash:
            conflict("request_conflict")
        return Admission(
            body, input_hash, replay=await comparison_snapshot(db, existing)
        )
    busy = await db.scalar(
        select(ComparisonRun).where(
            ComparisonRun.admin_id == user.id,
            ComparisonRun.status == "running",
        )
    )
    if busy:
        conflict("comparison_busy", comparison_run_id=busy.id)
    source = body.source_snapshot
    role = body.target_role
    if (
        COMPARISON_ROLES[role].target_pair
        and source.config.mentor.mode == "off"
    ):
        raise_input_error("mentor_disabled", "config.mentor.mode")
    if comparison is None and source.scenario_id is not None:
        scenario = await db.get(Scenario, source.scenario_id)
        if scenario is None or scenario.deleted_at is not None:
            raise HTTPException(404, detail={"code": "scenario_not_found"})
    if body.parent_run_id is not None:
        await owned_comparison(db, body.parent_run_id, user.id)
    return Admission(body, input_hash, comparison, previous, frozen)


async def allocate_panels(factory, db, user, body, prepared, frozen):
    role = body.target_role
    setting = await db.get(AppSetting, 1)
    if setting is None:
        raise HTTPException(503, detail={"code": "configuration_unavailable"})
    executions, frozen_panels = [], []
    try:
        for prepared_panel in prepared:
            panel = prepared_panel.panel
            connection, model = prepared_panel.connection, prepared_panel.model
            options, call = prepared_panel.options, prepared_panel.request
            permit = approve_call(
                factory,
                connection,
                setting,
                owner_id=user.id,
                operation=f"comparison_{role}",
                role=role,
                admin=True,
            )
            permit.model_options = options
            permit.model_config_id = model.id
            permit.config_version = model.config_version
            permit.model_id = model.model_id
            permit.capability_version = model.capability_definition_version
            permit.contract_version = ROLE_CONTRACT_VERSIONS[role]
            executions.append(
                dict(panel_id=panel.panel_id, request=call, permit=permit)
            )
            frozen_panels.append(
                dict(
                    panel_id=panel.panel_id,
                    model_config={
                        **panel.selection.model_dump(),
                        "options": options,
                    },
                    applied_options=prepared_panel.applied_options,
                    config_version=model.config_version,
                    connection_version=connection.connection_version,
                    capability_version=prepared_panel.capability_version,
                    contract_version=permit.contract_version,
                    prompt_version=COMPARISON_ROLES[role].prompt_version,
                    estimator_version=call.context_budget_json["estimator"],
                )
            )
            if frozen is not None and any(
                frozen[key] != frozen_panels[-1][key]
                for key in (
                    "prompt_version",
                    "contract_version",
                    "capability_version",
                    "estimator_version",
                )
            ):
                conflict("new_comparison_required")
    except BaseException as error:
        for execution in executions:
            execution["permit"].release()
        if not isinstance(error, InvocationError):
            raise
        raise HTTPException(
            429 if error.code == "call_limit_reached" else 503,
            detail={"code": error.code},
            headers=(
                {"Retry-After": "1"}
                if error.code == "call_limit_reached"
                else None
            ),
        ) from None
    return executions, frozen_panels


async def persist_comparison(
    db, user, admission, history, executions, frozen_panels
):
    body, input_hash = admission.body, admission.input_hash
    comparison, previous = admission.comparison, admission.previous
    source, role = body.source_snapshot, body.target_role
    try:
        frozen_source = source.model_dump()
        frozen_source["title"] = draft_title(source.title)
        created = utcnow()
        if comparison is None:
            comparison = ComparisonRun(
                id=str(uuid4()),
                admin_id=user.id,
                scenario_id=source.scenario_id,
                source_version=source.source_version,
                source_snapshot=frozen_source,
                source_hash=digest(frozen_source),
                target_role=role,
                mode="same_context",
                canonical_context=history,
                current_message=body.current_message,
                context_hash=digest(
                    dict(
                        instruction=getattr(
                            executions[0]["request"],
                            COMPARISON_ROLES[role].instruction_field,
                        ),
                        history=history,
                        current_message=body.current_message,
                        target_role=role,
                    )
                ),
                panel_configs=frozen_panels,
                request_id=body.request_id,
                input_hash=input_hash,
                parent_run_id=body.parent_run_id,
                created_at=created,
                expires_at=created + timedelta(days=30),
                status="running",
            )
            db.add(comparison)
        else:
            comparison.status = "running"
            created = max(
                created, previous.started_at + timedelta(microseconds=1)
            )
        await db.flush()
        turn_id = previous.turn_id if previous else str(uuid4())
        for frozen_panel, execution in zip(frozen_panels, executions):
            run = GenerationRun(
                id=str(uuid4()),
                owner_id=user.id,
                comparison_run_id=comparison.id,
                panel_id=frozen_panel["panel_id"],
                turn_id=turn_id,
                operation=role,
                mentor_trigger=(
                    source.config.mentor.mode
                    if COMPARISON_ROLES[role].target_pair
                    else None
                ),
                request_id=body.request_id,
                input_hash=input_hash,
                config_hash=digest(frozen_panel),
                provider=execution["request"].provider,
                model=execution["request"].model_id,
                status="running",
                started_at=created,
            )
            db.add(run)
            await db.flush()
        accepted = await comparison_snapshot(db, comparison)
        await db.commit()
    except BaseException as error:
        for execution in executions:
            execution["permit"].release()
        await db.invalidate()
        if isinstance(error, Exception):
            raise HTTPException(503, detail={"code": "storage_error"}) from None
        raise
    return accepted


async def finish_comparison(factory, run_id, **outcome):
    try:
        return await _finish_comparison(factory, run_id, **outcome)
    except Exception:
        logging.getLogger(__name__).exception(
            "Comparison terminal storage failed"
        )
        return await _finish_comparison(
            factory, run_id, status="interrupted", error_code="storage_error"
        )


async def _finish_comparison(
    factory,
    run_id,
    *,
    status,
    content=None,
    result=None,
    partial_text=None,
    error_code=None,
    first_output_at=None,
    provider_model_id=None,
    provider_request_id=None,
    mentor_result=None,
):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id)
        if run is None:
            return None
        comparison = await db.get(ComparisonRun, run.comparison_run_id)
        if comparison is None:
            return None
        unfinished_usage = await db.scalar(
            select(ApiUsageLog.id).where(
                ApiUsageLog.run_id == run_id, ApiUsageLog.status == "running"
            )
        )
        if unfinished_usage is not None:
            status, error_code = "interrupted", "storage_error"
            await db.execute(
                update(ApiUsageLog)
                .where(
                    ApiUsageLog.run_id == run_id,
                    ApiUsageLog.status == "running",
                )
                .values(
                    status="interrupted",
                    error_code="storage_error",
                    finished_at=utcnow(),
                )
            )
        if run.status == "running":
            actor = (
                await db.get(User, comparison.admin_id)
                if comparison.admin_id
                else None
            )
            if (
                actor is None
                or not actor.is_admin
                or comparison.expires_at <= utcnow()
            ):
                status, error_code = "interrupted", "configuration_unavailable"
            run.status, run.error_code = status, error_code
            run.first_output_at, run.finished_at = first_output_at, utcnow()
            run.partial_text = None if status == "completed" else partial_text
            public_result = dict(result=result) if result is not None else {}
            if status == "completed" and mentor_result is not None:
                run.mentor_reason_summary = mentor_result["reason_summary"]
                run.result_kind = (
                    "message"
                    if mentor_result["should_intervene"]
                    else "no_intervention"
                )
                public_result["result"] = dict(
                    should_intervene=mentor_result["should_intervene"],
                    coaching=mentor_result["feedback"],
                )
            run.outcome_json = (
                json.dumps(
                    dict(
                        text=content,
                        **public_result,
                        provider_model_id=provider_model_id,
                        provider_request_id=provider_request_id,
                    ),
                    ensure_ascii=False,
                )
                if status in COMPLETED_STATUSES
                else None
            )
        elif run.status == "cancelled" and partial_text is not None:
            # Stop commits the terminal first; the child still owns buffered text.
            run.partial_text, run.first_output_at = (
                partial_text,
                first_output_at,
            )
        await db.flush()
        result = await comparison_snapshot(db, comparison)
        if all(panel["status"] != "running" for panel in result["panels"]):
            comparison.status = result["status"] = "finished"
        try:
            await db.commit()
        except BaseException:
            await db.invalidate()
            raise
        return result

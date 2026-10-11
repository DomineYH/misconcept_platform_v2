"""Atomic comparison admission and durable results, without lesson writes."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from src.models import (
    AppSetting,
    ComparisonRun,
    GenerationRun,
    Scenario,
    User,
)
from src.services.call_admission import approve_call, execution_lock
from src.services.comparison_analysis import (
    PROMPT_VERSION as ANALYSIS_PROMPT_VERSION,
    analysis_comparison_request,
)
from src.services.comparison_state import (
    comparison_snapshot,
    owned_comparison,
    utcnow,
)
from src.services.context_budget import (
    estimate_input,
    input_budget,
    provider_parameters,
)
from src.services.generation_runs import conflict, digest
from src.services.invocation_types import InvocationError, TextRequest
from src.services.lesson_connections import resolve_frozen_model
from src.services.model_capabilities import (
    capabilities,
    validate_model_and_options,
)
from src.services.model_verification import ROLE_CONTRACT_VERSIONS
from src.services.scenario_publication import publication_errors
from src.services.student_bot import build_student_input

# Bump with changes to build_student_input's prompt contract.
PROMPT_VERSION = "student-v1"


def input_error(code, field, panel_id=None):
    raise HTTPException(
        422,
        detail={
            "code": code,
            "errors": [
                dict(
                    panel_id=panel_id,
                    field=field,
                    message="공통 입력과 선택 모델 옵션을 확인하세요.",
                )
            ],
        },
    )


async def reserve_comparison(factory, user, body):
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            actor = await db.get(User, user.id)
            if actor is None or not actor.is_admin:
                raise HTTPException(403, detail={"code": "admin_required"})
            input_hash = digest(body.model_dump(by_alias=True))
            existing = await db.scalar(
                select(ComparisonRun).where(
                    ComparisonRun.admin_id == user.id,
                    ComparisonRun.request_id == body.request_id,
                )
            )
            if existing:
                await owned_comparison(db, existing.id, user.id)
                if existing.input_hash != input_hash:
                    conflict("request_conflict")
                return await comparison_snapshot(db, existing), None
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
            if source.scenario_id is not None:
                scenario = await db.get(Scenario, source.scenario_id)
                if scenario is None or scenario.deleted_at is not None:
                    raise HTTPException(
                        404, detail={"code": "scenario_not_found"}
                    )
            if body.parent_run_id is not None:
                await owned_comparison(db, body.parent_run_id, user.id)
            errors = [
                error
                for error in publication_errors(source.config)
                if error["path"].startswith(
                    ("config.problem.", "config.student.", "config.analysis.")
                    if role == "analysis"
                    else ("config.problem.", "config.student.")
                )
                and not error["path"].endswith("resolved_model_config")
            ]
            if errors:
                input_error(errors[0]["code"], errors[0]["path"])
            history = [
                dict(id=index + 1, **message.model_dump())
                for index, message in enumerate(body.canonical_context)
            ]
            messages = build_student_input(
                SimpleNamespace(config=source.config),
                body.current_message,
                history,
            )
            prepared = []
            for panel in body.panels:
                if (
                    capabilities(
                        panel.selection.provider, panel.selection.model_id
                    )
                    is not None
                ):
                    try:
                        validate_model_and_options(
                            panel.selection.provider,
                            panel.selection.model_id,
                            panel.selection.options.model_dump(
                                exclude_unset=True
                            ),
                        )
                    except ValueError:
                        input_error(
                            "invalid_options", "model_config", panel.panel_id
                        )
                try:
                    connection, model, options = await resolve_frozen_model(
                        db, panel.selection, role
                    )
                    call = TextRequest(
                        connection.provider,
                        model.model_id,
                        "student",
                        messages[0]["content"],
                        messages[1:],
                        options,
                        body.request_id,
                    )
                    if role == "analysis":
                        call = analysis_comparison_request(
                            source,
                            history,
                            body.current_message,
                            panel,
                            options,
                            body.request_id,
                        )
                    budget, reserve, version = input_budget(call)
                    applied = provider_parameters(call)
                    if connection.provider == "google":
                        applied = applied["config"]
                    applied = {
                        key: value
                        for key, value in applied.items()
                        if key
                        in {
                            "max_output_tokens",
                            "max_tokens",
                            "temperature",
                            "reasoning",
                            "thinking",
                            "thinking_config",
                            "output_config",
                            "extra_body",
                        }
                    }
                    estimate = (
                        call.context_budget_json["estimated_input_tokens"]
                        if role == "analysis"
                        else estimate_input(call)
                    )
                    if estimate > budget:
                        input_error("context_limit", "history", panel.panel_id)
                    if role == "student":
                        call = replace(
                            call,
                            context_budget_json=dict(
                                estimator="utf8-v1",
                                estimated_input_tokens=estimate,
                                input_budget_tokens=budget,
                                reserved_output_tokens=reserve,
                                selected_prior_pairs=len(history) // 2,
                                kept_prior_pairs=len(history) // 2,
                                dropped_prior_pairs=0,
                                capability_definition_version=version,
                            ),
                        )
                except InvocationError as error:
                    if error.code in {"context_limit", "output_limit_missing"}:
                        input_error(
                            error.code,
                            (
                                "history"
                                if error.code == "context_limit"
                                else "model_config"
                            ),
                            panel.panel_id,
                        )
                    if error.code == "invalid_options":
                        input_error(error.code, "model_config", panel.panel_id)
                    raise HTTPException(
                        503, detail={"code": "configuration_unavailable"}
                    ) from None
                prepared.append(
                    (panel, connection, model, options, call, applied, version)
                )
            setting = await db.get(AppSetting, 1)
            if setting is None:
                raise HTTPException(
                    503, detail={"code": "configuration_unavailable"}
                )
            executions, frozen_panels = [], []
            try:
                for (
                    panel,
                    connection,
                    model,
                    options,
                    call,
                    applied,
                    version,
                ) in prepared:
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
                    permit.capability_version = (
                        model.capability_definition_version
                    )
                    permit.contract_version = ROLE_CONTRACT_VERSIONS[role]
                    executions.append(dict(request=call, permit=permit))
                    frozen_panels.append(
                        dict(
                            panel_id=panel.panel_id,
                            model_config={
                                **panel.selection.model_dump(),
                                "options": options,
                            },
                            applied_options=applied,
                            config_version=model.config_version,
                            connection_version=connection.connection_version,
                            capability_version=version,
                            contract_version=permit.contract_version,
                            prompt_version=(
                                ANALYSIS_PROMPT_VERSION
                                if role == "analysis"
                                else PROMPT_VERSION
                            ),
                            estimator_version=call.context_budget_json[
                                "estimator"
                            ],
                        )
                    )
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
            try:
                frozen_source = source.model_dump()
                frozen_source["title"] = (
                    source.title.strip() or "제목 없는 초안"
                )
                created = utcnow()
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
                            instruction=(
                                call.messages
                                if role == "analysis"
                                else messages[0]["content"]
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
                await db.flush()
                turn_id = str(uuid4())
                for frozen_panel, execution in zip(frozen_panels, executions):
                    run = GenerationRun(
                        id=str(uuid4()),
                        owner_id=user.id,
                        comparison_run_id=comparison.id,
                        panel_id=frozen_panel["panel_id"],
                        turn_id=turn_id,
                        operation=role,
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
                    raise HTTPException(
                        503, detail={"code": "storage_error"}
                    ) from None
                raise
            return accepted, executions


async def finish_comparison(
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
):
    async with factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        run = await db.get(GenerationRun, run_id)
        comparison = await db.get(ComparisonRun, run.comparison_run_id)
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
            run.outcome_json = (
                json.dumps(
                    dict(
                        text=content,
                        **(dict(result=result) if result is not None else {}),
                        provider_model_id=provider_model_id,
                        provider_request_id=provider_request_id,
                    ),
                    ensure_ascii=False,
                )
                if status in {"completed", "ok", "degraded"}
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

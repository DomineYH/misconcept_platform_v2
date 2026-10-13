"""Frozen setup, canonical dialogue and atomic action receipts."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from src.api.schemas.comparison import ComparisonRequest
from src.api.schemas.scenario_config import ScenarioConfig
from src.models import (
    ComparisonConversation,
    ComparisonRun,
    ModelConfig,
    ProviderConnection,
    User,
)
from src.services.call_admission import execution_lock
from src.services.comparison_mentor_policy import mentor_decisions
from src.services.comparison_preparation import (
    COMPARISON_ROLES,
    prepare_comparison,
    raise_input_error,
)
from src.services.comparison_runs import (
    admit_comparison,
    allocate_panels,
    persist_comparison,
)
from src.services.comparison_state import (
    comparison_busy,
    comparison_snapshot,
    find_panel,
    utcnow,
)
from src.services.generation_runs import conflict, digest
from src.services.scenario_drafts import native_scenario
from src.services.scenario_publication import publication_errors


def active_roles(source):
    return ["student", "analysis"] + (
        ["mentor"] if source["config"]["mentor"]["mode"] != "off" else []
    )


async def owned_conversation(db, identifier, user_id):
    actor = await db.get(User, user_id)
    if actor is None or not actor.is_admin:
        raise HTTPException(403, detail={"code": "admin_required"})
    parent = await db.get(ComparisonConversation, identifier)
    if (
        parent is None
        or parent.admin_id != user_id
        or parent.expires_at <= utcnow()
    ):
        raise HTTPException(404, detail={"code": "conversation_not_found"})
    return parent


async def children(db, parent):
    rows = (
        await db.scalars(
            select(ComparisonRun)
            .where(ComparisonRun.conversation_id == parent.id)
            .order_by(ComparisonRun.created_at, ComparisonRun.id)
        )
    ).all()
    return [await comparison_snapshot(db, child) for child in rows]


def pending_auto(parent):
    state = parent.state_json
    return bool(
        state["pairs"]
        and parent.source_snapshot["config"]["mentor"]["mode"] == "auto"
        and not any(
            action["target_turn_id"] == state["pairs"][-1]["turn_id"]
            and action["trigger"] == "auto"
            for action in state["mentor_actions"]
        )
    )


async def conversation_snapshot(db, parent):
    rounds = await children(db, parent)
    state = parent.state_json
    running = any(comparison_busy(child) for child in rounds)
    pending = next(
        (
            child
            for child in rounds
            if child["comparison_run_id"] == state["pending_student_child_id"]
        ),
        None,
    )
    phase = state["dialogue_status"]
    if running:
        phase = (
            "analyzing" if state["dialogue_status"] == "ended" else "generating"
        )
    elif pending:
        phase = (
            "awaiting_adoption"
            if any(
                panel["status"] == "completed" for panel in pending["panels"]
            )
            else "retry_required"
        )
    return dict(
        conversation_id=parent.id,
        version=parent.version,
        source_snapshot=parent.source_snapshot,
        source_version=parent.source_version,
        source_hash=parent.source_hash,
        panels=parent.panel_configs,
        created_at=parent.created_at.isoformat(),
        expires_at=parent.expires_at.isoformat(),
        **{key: value for key, value in state.items() if key != "receipts"},
        children=rounds,
        phase=phase,
        pending_auto=pending_auto(parent),
        allowed_actions=dict(
            turn=not running
            and not pending
            and phase == "open"
            and not pending_auto(parent),
            adopt=phase == "awaiting_adoption",
            mentor=not running
            and not pending
            and phase == "open"
            and bool(state["pairs"])
            and parent.source_snapshot["config"]["mentor"]["mode"] != "off",
            end=not running
            and not pending
            and phase == "open"
            and bool(state["pairs"])
            and not pending_auto(parent),
        ),
    )


def role_body(
    parent, role, request_id, *, message=None, panels=None, trigger=None
):
    history = [
        dict(role=role_name, content=pair[key])
        for pair in parent.state_json["pairs"]
        for role_name, key in [("teacher", "teacher"), ("student", "student")]
    ]
    selected = panels if panels is not None else parent.panel_configs
    try:
        return ComparisonRequest.model_validate(
            dict(
                request_id=str(request_id),
                target_role=role,
                source_snapshot=parent.source_snapshot,
                canonical_context=history,
                current_message=message,
                mentor_trigger=trigger,
                panels=[
                    dict(
                        panel_id=panel["panel_id"],
                        model_config={
                            **panel["identity"],
                            "options": panel["role_options"][role],
                        },
                    )
                    for panel in selected
                ],
            )
        )
    except ValueError:
        raise_input_error("invalid_input", "panels")


async def create_conversation(factory, user, body):
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            actor = await db.get(User, user.id)
            if actor is None or not actor.is_admin:
                raise HTTPException(403, detail={"code": "admin_required"})
            input_hash = digest(body.model_dump(mode="json"))
            existing = await db.scalar(
                select(ComparisonConversation).where(
                    ComparisonConversation.admin_id == user.id,
                    ComparisonConversation.request_id == str(body.request_id),
                )
            )
            if existing:
                await owned_conversation(db, existing.id, user.id)
                if existing.input_hash != input_hash:
                    conflict("request_conflict")
                return await conversation_snapshot(db, existing), False
            scenario = await native_scenario(
                db, body.scenario_id, body.expected_scenario_version
            )
            source = dict(
                title=scenario.title,
                subject=scenario.subject or "",
                target_grade=scenario.target_grade or "",
                scenario_id=scenario.id,
                source_version=scenario.config_version,
                source_kind="saved_scenario",
                config_schema_version=1,
                config=deepcopy(scenario.config_json),
            )
            errors = [
                error
                for error in publication_errors(
                    ScenarioConfig.model_validate(source["config"])
                )
                if not error["path"].endswith("resolved_model_config")
            ]
            if errors:
                raise_input_error(errors[0]["code"], errors[0]["path"])
            panels = []
            for panel in body.panels:
                model = await db.get(ModelConfig, panel.model_config_id)
                connection = (
                    await db.get(
                        ProviderConnection, model.provider_connection_id
                    )
                    if model
                    else None
                )
                if connection is None:
                    raise_input_error(
                        "configuration_unavailable", "panels", panel.panel_id
                    )
                identity = dict(
                    model_config_id=model.id,
                    provider_connection_id=connection.id,
                    provider=connection.provider,
                    model_id=model.model_id,
                )
                panels.append(
                    dict(
                        panel_id=panel.panel_id,
                        identity=identity,
                        role_options={
                            role: (
                                panel.role_options[role].model_dump(
                                    exclude_unset=True
                                )
                                if role in panel.role_options
                                else deepcopy(model.default_options_json)
                            )
                            for role in ("student", "mentor", "analysis")
                        },
                        stamps={},
                    )
                )
            created = utcnow()
            parent = ComparisonConversation(
                id=str(uuid4()),
                admin_id=user.id,
                scenario_id=scenario.id,
                source_version=scenario.config_version,
                source_snapshot=source,
                source_hash=digest(source),
                panel_configs=panels,
                state_json=dict(
                    schema_version=1,
                    dialogue_status="open",
                    pairs=[],
                    pending_student_child_id=None,
                    mentor_actions=[],
                    final_analysis_child_id=None,
                    receipts={},
                ),
                version=1,
                request_id=str(body.request_id),
                input_hash=input_hash,
                created_at=created,
                expires_at=created + timedelta(days=30),
            )
            # Prepare without permits or providers, freezing each active role's contracts.
            parent.state_json["pairs"] = [
                dict(teacher="Setup", student="Setup")
            ]
            for role in active_roles(source):
                draft = role_body(
                    parent,
                    role,
                    body.request_id,
                    message="Setup" if role == "student" else None,
                )
                _, prepared = await prepare_comparison(db, draft)
                for panel, item in zip(panels, prepared):
                    panel["role_options"][role] = item.options
                    panel["stamps"][role] = dict(
                        prompt_version=COMPARISON_ROLES[role].prompt_version,
                        contract_version=item.model.verification_state[role][
                            "role_contract_version"
                        ],
                        capability_version=item.capability_version,
                        estimator_version=item.request.context_budget_json[
                            "estimator"
                        ],
                    )
            parent.state_json["pairs"] = []
            db.add(parent)
            await db.flush()
            result = await conversation_snapshot(db, parent)
            await db.commit()
            return result, True


async def action_context(db, user, identifier, action, body):
    parent = await owned_conversation(db, identifier, user.id)
    input_hash = digest(dict(action=action, **body.model_dump(mode="json")))
    receipt = parent.state_json["receipts"].get(str(body.request_id))
    if receipt:
        if receipt["input_hash"] != input_hash:
            conflict("request_conflict")
        return parent, input_hash, True
    if parent.version != body.expected_version:
        conflict("version_conflict", current_version=parent.version)
    return parent, input_hash, False


def record_action(parent, state, action, body, input_hash, child_id=None):
    parent.version += 1
    state["receipts"][str(body.request_id)] = dict(
        action=action,
        input_hash=input_hash,
        accepted_version=parent.version,
        child_id=child_id
        or (str(body.child_id) if hasattr(body, "child_id") else None),
        targets={
            key: str(getattr(body, key))
            for key in ("target_turn_id", "panel_id", "generation_id")
            if hasattr(body, key)
        },
    )
    parent.state_json = state


async def reserve_child(factory, db, user, parent, body):
    admission = await admit_comparison(
        db, user, body, None, None, conversation_id=parent.id
    )
    history, prepared = await prepare_comparison(db, body)
    executions, frozen = await allocate_panels(
        factory, db, user, body, prepared, None
    )
    try:
        for panel in frozen:
            setup = next(
                p
                for p in parent.panel_configs
                if p["panel_id"] == panel["panel_id"]
            )
            if any(
                panel[key] != value
                for key, value in setup["stamps"][body.target_role].items()
            ):
                conflict("new_comparison_required")
        child = await persist_comparison(
            db,
            user,
            admission,
            history,
            executions,
            frozen,
            conversation=parent,
            commit=False,
        )
        return child, executions
    except BaseException:
        for execution in executions:
            execution["permit"].release()
        raise


async def dialogue_action(factory, user, identifier, action, body):
    executions = None
    async with execution_lock():
        async with factory() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            parent, input_hash, replay = await action_context(
                db, user, identifier, action, body
            )
            if replay:
                return await conversation_snapshot(db, parent), None
            if action != "mentor":
                await validate_setup(
                    db, parent, body.panel_id if action == "adopt" else None
                )
            state = deepcopy(parent.state_json)
            rounds = await children(db, parent)
            if state["dialogue_status"] != "open":
                conflict("dialogue_ended")
            if any(comparison_busy(child) for child in rounds):
                conflict("comparison_busy")
            child = None
            if action == "turn":
                if state["pending_student_child_id"] or pending_auto(parent):
                    conflict("adoption_or_auto_required")
                if not body.message.strip() or len(state["pairs"]) >= 100:
                    raise_input_error("invalid_message", "message")
                child, executions = await reserve_child(
                    factory,
                    db,
                    user,
                    parent,
                    role_body(
                        parent, "student", body.request_id, message=body.message
                    ),
                )
                state["pending_student_child_id"] = child["comparison_run_id"]
            elif action == "adopt":
                if state["pending_student_child_id"] != str(body.child_id):
                    conflict("stale_generation")
                pending = next(
                    child
                    for child in rounds
                    if child["comparison_run_id"] == str(body.child_id)
                )
                panel = find_panel(pending, body.panel_id)
                if (
                    panel["run_id"] != str(body.generation_id)
                    or panel["status"] != "completed"
                ):
                    conflict("stale_generation")
                state["pairs"].append(
                    dict(
                        turn_id=panel["turn_id"],
                        position=len(state["pairs"]) + 1,
                        teacher=pending["current_message"],
                        student=panel["text"],
                        adopted_child_id=str(body.child_id),
                        adopted_panel_id=body.panel_id,
                        adopted_generation_id=str(body.generation_id),
                    )
                )
                state["pending_student_child_id"] = None
            elif action == "mentor":
                mode = parent.source_snapshot["config"]["mentor"]["mode"]
                if mode == "off" or (body.trigger == "auto" and mode != "auto"):
                    raise_input_error("mentor_disabled", "trigger")
                if (
                    state["pending_student_child_id"]
                    or not state["pairs"]
                    or state["pairs"][-1]["turn_id"] != str(body.target_turn_id)
                ):
                    conflict("stale_target")
                existing = next(
                    (
                        entry
                        for entry in state["mentor_actions"]
                        if entry["trigger"] == "auto"
                        and entry["target_turn_id"] == str(body.target_turn_id)
                    ),
                    None,
                )
                if body.trigger == "auto" and existing:
                    decisions = existing["panel_decisions"]
                    child_id = existing["child_id"]
                else:
                    decisions = mentor_decisions(parent, rounds, body.trigger)
                    admitted = [
                        panel
                        for panel in parent.panel_configs
                        if decisions[panel["panel_id"]] == "admitted"
                    ]
                    if admitted:
                        for panel in admitted:
                            await validate_setup(db, parent, panel["panel_id"])
                        child, executions = await reserve_child(
                            factory,
                            db,
                            user,
                            parent,
                            role_body(
                                parent,
                                "mentor",
                                body.request_id,
                                panels=admitted,
                                trigger=body.trigger,
                            ),
                        )
                    child_id = child["comparison_run_id"] if child else None
                    state["mentor_actions"].append(
                        dict(
                            action_id=str(body.request_id),
                            target_turn_id=str(body.target_turn_id),
                            position=state["pairs"][-1]["position"],
                            trigger=body.trigger,
                            child_id=child_id,
                            panel_decisions=decisions,
                        )
                    )
            elif action == "end":
                if (
                    not state["pairs"]
                    or state["pending_student_child_id"]
                    or pending_auto(parent)
                ):
                    conflict("adoption_or_auto_required")
                child, executions = await reserve_child(
                    factory,
                    db,
                    user,
                    parent,
                    role_body(parent, "analysis", body.request_id),
                )
                state["dialogue_status"] = "ended"
                state["final_analysis_child_id"] = child["comparison_run_id"]
            else:
                raise ValueError("unsupported dialogue action")
            record_action(
                parent,
                state,
                action,
                body,
                input_hash,
                (
                    child["comparison_run_id"]
                    if child
                    else (child_id if action == "mentor" else None)
                ),
            )
            try:
                await db.flush()
                result = await conversation_snapshot(db, parent)
                await db.commit()
            except BaseException:
                if executions:
                    for execution in executions:
                        execution["permit"].release()
                raise
            if executions:
                child.update(
                    conversation_id=parent.id,
                    version=parent.version,
                    receipt=state["receipts"][str(body.request_id)],
                )
                return child, executions
            return result, None


async def conversation_model(db, parent, panel_id):
    from src.api.schemas.scenario_config import ResolvedModelConfig
    from src.models import Scenario
    from src.services.invocation_types import InvocationError
    from src.services.lesson_connections import resolve_frozen_model

    source = (
        await db.get(Scenario, parent.scenario_id)
        if parent.scenario_id
        else None
    )
    if source is None or source.deleted_at is not None:
        raise HTTPException(404, detail={"code": "scenario_not_found"})
    panel = next(
        (
            panel
            for panel in parent.panel_configs
            if panel["panel_id"] == panel_id
        ),
        None,
    )
    if panel is None:
        raise HTTPException(404, detail={"code": "panel_not_found"})
    try:
        for role in set(active_roles(parent.source_snapshot)) | set(
            active_roles({"config": source.config_json})
        ):
            await resolve_frozen_model(
                db,
                ResolvedModelConfig.model_validate(
                    {
                        **panel["identity"],
                        "options": panel["role_options"][role],
                    }
                ),
                role,
            )
    except (InvocationError, ValueError):
        raise HTTPException(
            422, detail={"code": "configuration_unavailable"}
        ) from None
    return dict(
        scenario_id=source.id,
        identity=panel["identity"],
        role_options=panel["role_options"],
    )


async def validate_setup(db, parent, panel_id=None):
    from src.api.schemas.scenario_config import ResolvedModelConfig
    from src.services.invocation_types import InvocationError
    from src.services.lesson_connections import resolve_frozen_model

    try:
        for panel in parent.panel_configs:
            if panel_id is not None and panel["panel_id"] != panel_id:
                continue
            for role in active_roles(parent.source_snapshot):
                selection = ResolvedModelConfig.model_validate(
                    {
                        **panel["identity"],
                        "options": panel["role_options"][role],
                    }
                )
                await resolve_frozen_model(db, selection, role)
    except (InvocationError, ValueError):
        raise_input_error("configuration_unavailable", "panels")

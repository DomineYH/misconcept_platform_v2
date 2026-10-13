"""Role-specific comparison requests and all-panel validation before admission."""

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Callable

from fastapi import HTTPException

from src.api.schemas.comparison import ComparisonPanel
from src.models import ModelConfig, ProviderConnection
from src.services.comparison_analysis import (
    PROMPT_VERSION as ANALYSIS_PROMPT_VERSION,
)
from src.services.comparison_analysis import analysis_comparison_request
from src.services.context_budget import (
    estimate_input,
    input_budget,
    provider_parameters,
)
from src.services.invocation_types import InvocationError, TextRequest
from src.services.lesson_connections import resolve_frozen_model
from src.services.model_capabilities import (
    capabilities,
    validate_model_and_options,
)
from src.services.scenario_publication import publication_errors
from src.services.student_bot import build_student_input
from src.services.tutor_bot import build_mentor_input

# Bump with changes to build_student_input's prompt contract.
STUDENT_PROMPT_VERSION = "student-v1"
MENTOR_PROMPT_VERSION = "mentor-v1"


@dataclass(frozen=True)
class PreparedPanel:
    panel: ComparisonPanel
    connection: ProviderConnection
    model: ModelConfig
    options: dict
    request: TextRequest
    applied_options: dict
    capability_version: str


@dataclass(frozen=True)
class ComparisonRole:
    prompt_version: str
    build_request: Callable
    estimate: Callable
    paths: tuple[str, ...]
    target_pair: bool = False
    instruction_field: str = "system_instruction"


def student_request(body, history, panel, options):
    messages = build_student_input(
        SimpleNamespace(config=body.source_snapshot.config),
        body.current_message,
        history,
    )
    return TextRequest(
        panel.selection.provider,
        panel.selection.model_id,
        "student",
        messages[0]["content"],
        messages[1:],
        options,
        body.request_id,
    )


def mentor_request(body, history, panel, options):
    source = body.source_snapshot
    return build_mentor_input(
        source.config,
        history,
        body.mentor_trigger or source.config.mentor.mode,
        panel.selection,
        options,
        body.request_id,
    )


def analysis_request(body, history, panel, options):
    return analysis_comparison_request(
        body.source_snapshot,
        history,
        body.current_message,
        panel,
        options,
        body.request_id,
    )


COMMON_PATHS = ("config.problem.", "config.student.")
COMPARISON_ROLES = {
    "student": ComparisonRole(
        STUDENT_PROMPT_VERSION, student_request, estimate_input, COMMON_PATHS
    ),
    "mentor": ComparisonRole(
        MENTOR_PROMPT_VERSION,
        mentor_request,
        estimate_input,
        COMMON_PATHS + ("config.mentor.",),
        target_pair=True,
    ),
    "analysis": ComparisonRole(
        ANALYSIS_PROMPT_VERSION,
        analysis_request,
        lambda call: call.context_budget_json["estimated_input_tokens"],
        COMMON_PATHS + ("config.analysis.",),
        instruction_field="messages",
    ),
}


def raise_input_error(code, field, panel_id=None):
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


async def prepare_comparison(db, body):
    source, role = body.source_snapshot, body.target_role
    settings = COMPARISON_ROLES[role]
    errors = [
        error
        for error in publication_errors(source.config)
        if error["path"].startswith(settings.paths)
        and not error["path"].endswith("resolved_model_config")
    ]
    if errors:
        raise_input_error(errors[0]["code"], errors[0]["path"])
    history = [
        dict(id=index + 1, **message.model_dump())
        for index, message in enumerate(body.canonical_context)
    ]
    prior_pairs = len(history) // 2 - int(settings.target_pair)
    prepared = []
    for panel in body.panels:
        if (
            capabilities(panel.selection.provider, panel.selection.model_id)
            is not None
        ):
            try:
                validate_model_and_options(
                    panel.selection.provider,
                    panel.selection.model_id,
                    panel.selection.options.model_dump(exclude_unset=True),
                )
            except ValueError:
                raise_input_error(
                    "invalid_options", "model_config", panel.panel_id
                )
        try:
            connection, model, options = await resolve_frozen_model(
                db, panel.selection, role
            )
            call = settings.build_request(body, history, panel, options)
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
            if "output_config" in applied:
                applied["output_config"] = {
                    key: value
                    for key, value in applied["output_config"].items()
                    if key != "format"
                }
                if not applied["output_config"]:
                    del applied["output_config"]
            estimate = settings.estimate(call)
            if estimate > budget:
                raise_input_error("context_limit", "history", panel.panel_id)
            if call.context_budget_json is None:
                call = replace(
                    call,
                    context_budget_json=dict(
                        estimator="utf8-v1",
                        estimated_input_tokens=estimate,
                        input_budget_tokens=budget,
                        reserved_output_tokens=reserve,
                        selected_prior_pairs=prior_pairs,
                        kept_prior_pairs=prior_pairs,
                        dropped_prior_pairs=0,
                        target_pair_included=settings.target_pair,
                        capability_definition_version=version,
                    ),
                )
        except InvocationError as error:
            if error.code in {"context_limit", "output_limit_missing"}:
                raise_input_error(
                    error.code,
                    (
                        "history"
                        if error.code == "context_limit"
                        else "model_config"
                    ),
                    panel.panel_id,
                )
            if error.code == "invalid_options":
                raise_input_error(error.code, "model_config", panel.panel_id)
            raise HTTPException(
                503, detail={"code": "configuration_unavailable"}
            ) from None
        prepared.append(
            PreparedPanel(
                panel, connection, model, options, call, applied, version
            )
        )
    return history, prepared

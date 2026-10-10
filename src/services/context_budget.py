"""Conservative local input estimate; never actual usage or a tokenizer count."""

import json
from dataclasses import replace

from src.services.invocation_types import InvocationError, StructuredRequest
from src.services.model_capabilities import capabilities


def provider_parameters(request):
    from src.services.call_execution import PROVIDER_ADAPTERS

    return PROVIDER_ADAPTERS[request.provider][1].parameters(request)


def provider_schema(request):
    """Reuse the exact schema conversion that will be sent by the adapter."""
    parameters = provider_parameters(request)
    if request.provider == "openai":
        return parameters["text"]["format"]["schema"]
    if request.provider == "anthropic":
        return parameters["output_config"]["format"]["schema"]
    return parameters["config"]["response_json_schema"]


def estimate_input(request):
    envelope = dict(
        system_instruction=request.system_instruction, messages=request.messages
    )
    if isinstance(request, StructuredRequest):
        envelope["output_schema"] = provider_schema(request)
    encoded = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return len(encoded) + 32 * len(request.messages) + 1024


def input_budget(request):
    """Return input budget, output reserve and definition version for any role."""
    definition = capabilities(request.provider, request.model_id)
    if definition is None:
        raise InvocationError("configuration_unavailable")
    parameters = provider_parameters(request)
    if request.provider == "anthropic":
        reserve = parameters["max_tokens"]
    elif request.provider == "google":
        reserve = parameters["config"].get(
            "max_output_tokens", definition["max_output_tokens"]
        )
    else:
        reserve = parameters.get(
            "max_output_tokens", definition["max_output_tokens"]
        )
    limits = []
    for name in ("input_token_limit", "combined_context_tokens"):
        value = definition.get(name)
        if value is not None:
            if type(value) is not int or value <= 0:
                raise InvocationError("configuration_unavailable")
            limits.append(
                value - reserve if name == "combined_context_tokens" else value
            )
    if not limits or min(limits) <= 0:
        raise InvocationError("configuration_unavailable")
    return min(limits), reserve, definition["definition_version"]


def fit_context(
    request,
    *,
    prior_pair_count,
    configured_prior_turn_limit,
    target_pair_included=False,
    rebuild_messages=None,
):
    """Trim oldest pairs, optionally rebuilding messages for the final payload."""
    budget, reserve, version = input_budget(request)
    estimate = estimate_input(request)
    dropped = 0
    while estimate > budget:
        if dropped == prior_pair_count:
            raise InvocationError("context_limit")
        dropped += 1
        request = replace(
            request,
            messages=(
                rebuild_messages(dropped)
                if rebuild_messages is not None
                else request.messages[2:]
            ),
        )
        estimate = estimate_input(request)
    return replace(
        request,
        context_budget_json=dict(
            estimator="utf8-v1",
            estimated_input_tokens=estimate,
            input_budget_tokens=budget,
            reserved_output_tokens=reserve,
            configured_prior_turn_limit=configured_prior_turn_limit,
            selected_prior_pairs=prior_pair_count,
            kept_prior_pairs=prior_pair_count - dropped,
            dropped_prior_pairs=dropped,
            target_pair_included=target_pair_included,
            capability_definition_version=version,
        ),
    )

"""Claude input components add; output includes thinking; cumulative values replace."""

from src.services.anthropic_capabilities import MODEL

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


def normalize_usage(usage, previous=None, *, model_id=None):
    raw = dict((previous or {}).get("raw_usage_json") or {})
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = getattr(usage, name, None)
        if type(value) is int and value >= 0:
            raw[name] = value
    thinking = getattr(
        getattr(usage, "output_tokens_details", None), "thinking_tokens", None
    )
    if type(thinking) is int and thinking >= 0:
        raw["thinking_tokens"] = thinking
    for name in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
        value = getattr(getattr(usage, "cache_creation", None), name, None)
        if type(value) is int and value >= 0:
            raw[name] = value
    for name, allowed in (
        ("service_tier", ("standard", "priority", "batch")),
        ("inference_geo", ("global", "us")),
    ):
        value = getattr(usage, name, None)
        if value in allowed:
            raw[name] = value
    components = [
        raw.get(name)
        for name in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    ]
    total_input = (
        sum(components)
        if all(value is not None for value in components)
        else None
    )
    output = raw.get("output_tokens")
    reasoning = raw.get("thinking_tokens")
    if reasoning is not None and output is not None and reasoning > output:
        reasoning = None
    total = (
        total_input + output
        if total_input is not None and output is not None
        else None
    )
    cost = None
    if (
        model_id == MODEL
        and raw.get("service_tier") == "standard"
        and raw.get("inference_geo") == "global"
        and total is not None
    ):
        five = raw.get("ephemeral_5m_input_tokens")
        hour = raw.get("ephemeral_1h_input_tokens")
        write = raw["cache_creation_input_tokens"]
        if write == 0 and five in (None, 0) and hour in (None, 0):
            five = hour = 0
        if five is not None and hour is not None and five + hour == write:
            cost = (
                raw["input_tokens"] * 3
                + raw["cache_read_input_tokens"] * 0.30
                + five * 3.75
                + hour * 6
                + output * 15
            ) / 1000000
    return dict(
        input_tokens=total_input,
        output_tokens=output,
        total_tokens=total,
        cache_read_tokens=raw.get("cache_read_input_tokens"),
        cache_write_tokens=raw.get("cache_creation_input_tokens"),
        reasoning_tokens=reasoning,
        usage_complete=total is not None and reasoning is not None,
        raw_usage_json=raw or None,
        estimated_cost_usd=cost,
        pricing_as_of="2026-10-09" if cost is not None else None,
        pricing_source=PRICING_SOURCE if cost is not None else None,
    )

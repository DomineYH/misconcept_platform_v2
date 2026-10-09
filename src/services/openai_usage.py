"""Observed OpenAI token totals; cache/reasoning are subsets, never additions."""

# Standard USD / million tokens, checked 2026-10-09; see docs/ai-usage-pricing.md.
PRICES = {
    "gpt-5-mini": (0.25, 0.025, 2.0, "gpt-5-mini"),
    "gpt-5-mini-2025-08-07": (0.25, 0.025, 2.0, "gpt-5-mini"),
    "gpt-5.2": (1.75, 0.175, 14.0, "gpt-5.2"),
    "gpt-5.2-2025-12-11": (1.75, 0.175, 14.0, "gpt-5.2"),
}


def normalize_usage(usage, previous=None, *, model_id=None, service_tier=None):
    def number(value):
        return value if type(value) is int and value >= 0 else None

    def get(item, key):
        return getattr(item, key, None) if item is not None else None

    values = dict(
        input_tokens=number(get(usage, "input_tokens")),
        output_tokens=number(get(usage, "output_tokens")),
        total_tokens=number(get(usage, "total_tokens")),
        cache_read_tokens=number(
            get(get(usage, "input_tokens_details"), "cached_tokens")
        ),
        reasoning_tokens=number(
            get(get(usage, "output_tokens_details"), "reasoning_tokens")
        ),
        cache_write_tokens=None,
    )
    if previous:
        values = {
            key: value if value is not None else previous.get(key)
            for key, value in values.items()
        }
    for detail, total in [
        ("cache_read_tokens", "input_tokens"),
        ("reasoning_tokens", "output_tokens"),
    ]:
        if (
            values[detail] is not None
            and values[total] is not None
            and values[detail] > values[total]
        ):
            values[detail] = None
    if all(
        values[k] is not None
        for k in ("input_tokens", "output_tokens", "total_tokens")
    ):
        if (
            values["total_tokens"]
            != values["input_tokens"] + values["output_tokens"]
        ):
            values["total_tokens"] = None
    values["usage_complete"] = all(
        values[k] is not None
        for k in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "reasoning_tokens",
        )
    )
    raw = (
        {k: v for k, v in values.items() if type(v) is int}
        if usage is not None or previous is not None
        else None
    )
    if raw is not None:
        prior_raw = (previous or {}).get("raw_usage_json") or {}
        for key, value, allowed in (
            ("pricing_model", model_id, PRICES),
            (
                "service_tier",
                service_tier,
                ("default", "auto", "flex", "priority"),
            ),
        ):
            if value is not None:
                raw[key] = (
                    value
                    if isinstance(value, str) and value in allowed
                    else None
                )
            else:
                raw[key] = prior_raw.get(key)
    values["raw_usage_json"] = raw
    price = PRICES.get((raw or {}).get("pricing_model"))
    cost = None
    if (
        price
        and (raw or {}).get("service_tier") == "default"
        and all(
            values[k] is not None
            for k in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_tokens",
            )
        )
    ):
        cost = (
            (values["input_tokens"] - values["cache_read_tokens"]) * price[0]
            + values["cache_read_tokens"] * price[1]
            + values["output_tokens"] * price[2]
        ) / 1000000
    values.update(
        estimated_cost_usd=cost,
        pricing_as_of="2026-10-09" if cost is not None else None,
        pricing_source=(
            f"https://developers.openai.com/api/docs/models/{price[3]}"
            if cost is not None
            else None
        ),
    )
    return values

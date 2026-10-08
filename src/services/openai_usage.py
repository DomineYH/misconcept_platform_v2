"""Observed OpenAI token totals; cache/reasoning are subsets, never additions."""


def normalize_usage(usage, previous=None):
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
    values["raw_usage_json"] = (
        {k: v for k, v in values.items() if type(v) is int}
        if usage is not None or previous is not None
        else None
    )
    return values

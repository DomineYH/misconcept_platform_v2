"""Gemini prompt includes cache; output is candidates plus thoughts."""


def normalize_usage(usage, previous=None):
    names = (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "cached_content_token_count",
        "total_token_count",
    )
    raw = dict((previous or {}).get("raw_usage_json") or {})
    for name in names:
        value = getattr(usage, name, None)
        if type(value) is int and value >= 0:
            raw[name] = value
    candidates, thoughts = raw.get("candidates_token_count"), raw.get(
        "thoughts_token_count"
    )
    values = dict(
        input_tokens=raw.get("prompt_token_count"),
        output_tokens=(
            candidates + thoughts
            if candidates is not None and thoughts is not None
            else None
        ),
        reasoning_tokens=thoughts,
        cache_read_tokens=raw.get("cached_content_token_count"),
        cache_write_tokens=None,
        total_tokens=raw.get("total_token_count"),
    )
    if (
        values["cache_read_tokens"] is not None
        and values["input_tokens"] is not None
        and values["cache_read_tokens"] > values["input_tokens"]
    ):
        values["cache_read_tokens"] = None
    if (
        all(
            values[k] is not None
            for k in ("input_tokens", "output_tokens", "total_tokens")
        )
        and values["total_tokens"]
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
        raw if usage is not None or previous is not None else None
    )
    return values

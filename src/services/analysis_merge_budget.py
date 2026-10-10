"""Bound the future merge request by the authoritative feedback schema."""

import json

from src.services.analysis_output_contract import MergeAnalysisOutput
from src.utils.cache import load_prompt_template


def maximum_feedback(messages):
    """Worst serialized strings include six-byte JSON control escapes."""
    schema = MergeAnalysisOutput.model_json_schema()
    longest_id = max(
        [m["id"] for m in messages], key=lambda mid: len(str(mid)), default=0
    )

    quote_limit = min(
        200, max((len(m["content"]) for m in messages), default=0)
    )

    def maximum(node, field=None):
        if "$ref" in node:
            return maximum(schema["$defs"][node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            return max(
                (maximum(n) for n in node["anyOf"]),
                key=lambda v: len(json.dumps(v)),
            )
        if "enum" in node:
            return max(node["enum"], key=lambda v: len(json.dumps(v)))
        if node["type"] == "object":
            return {
                key: maximum(value, key)
                for key, value in node["properties"].items()
            }
        if node["type"] == "array":
            return [maximum(node["items"]) for _ in range(node["maxItems"])]
        if node["type"] == "string":
            return "\x00" * (
                quote_limit if field == "quote" else node["maxLength"]
            )
        if node["type"] == "integer":
            return node.get("maximum", longest_id)
        raise ValueError("Unbounded merge schema")

    return maximum(schema)


def merge_prompt(snapshot, feedback, statistics, coverage):
    inputs = dict(
        analysis=snapshot.config.analysis.model_dump(
            exclude={"resolved_model_config"}
        ),
        chunk_feedback=feedback,
        statistics=statistics,
        coverage=coverage,
    )
    return (
        load_prompt_template("analysis_merge_v2.txt")
        + "\n입력 JSON\n"
        + json.dumps(inputs, ensure_ascii=False)
    )

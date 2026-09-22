"""Helper functions for analysis-related operations."""

import json
from typing import Optional


def parse_reasoning(meta_json: Optional[str]) -> Optional[dict]:
    """Parse meta_json into the slim reasoning structure.

    Returns a dict with exactly ``summary`` and ``improved_sentence`` keys.
    Legacy ``meta_json`` rows that still contain per-domain blocks
    (``pedagogical`` / ``cognitive`` / ``contextual``) have those keys
    silently dropped — UI/templates must not depend on them anymore.

    Args:
        meta_json: JSON string containing reasoning information

    Returns:
        Dictionary with reasoning structure or None if no input
    """
    if not meta_json:
        return None
    try:
        parsed = json.loads(meta_json)
    except json.JSONDecodeError:
        return {"summary": meta_json, "improved_sentence": None}

    if isinstance(parsed, dict):
        return {
            "summary": parsed.get("summary", ""),
            "improved_sentence": parsed.get("improved_sentence"),
        }
    return {
        "summary": str(parsed) if parsed else "",
        "improved_sentence": None,
    }

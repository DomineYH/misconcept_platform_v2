"""Session feedback utility functions for issue #28.

Provides helpers for deriving plain feedback, loading structured
reports, validating payloads, and computing prompt hashes.
"""

import json
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

FALLBACK_FEEDBACK = (
    "분석에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
)


def derive_plain_feedback(payload: dict) -> str:
    """Derive a ≤500-char human-readable sentence from payload.

    Uses payload.brief_feedback[0] if available.
    Rejects JSON-looking strings (starts with { or [).
    Normalizes whitespace.
    Falls back to FALLBACK_FEEDBACK on empty/invalid input.
    """
    bf = payload.get("brief_feedback", [])
    if not bf or not isinstance(bf, list):
        return FALLBACK_FEEDBACK

    first = bf[0]
    if not isinstance(first, str):
        return FALLBACK_FEEDBACK

    stripped = first.strip()
    if not stripped:
        return FALLBACK_FEEDBACK

    # Reject JSON-looking content
    if stripped.startswith("{") or stripped.startswith("["):
        return FALLBACK_FEEDBACK

    # Normalize whitespace
    normalized = re.sub(r"\s+", " ", stripped)

    # Clamp to 500 chars
    if len(normalized) > 500:
        normalized = normalized[:497] + "..."

    return normalized


async def load_feedback_sections(
    session_id: int, db: AsyncSession
) -> Optional[dict]:
    """Fetch SessionFeedbackReport and return payload dict.

    Returns None for legacy sessions (no report row).
    """
    from src.models.session_feedback_report import (
        SessionFeedbackReport,
    )

    result = await db.execute(
        select(SessionFeedbackReport).where(
            SessionFeedbackReport.session_id == session_id
        )
    )
    report = result.scalar_one_or_none()
    if report is None:
        return None

    try:
        payload = json.loads(report.payload_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid payload_json for session %d",
            session_id,
        )
        return None

    return {
        "brief_feedback": payload.get("brief_feedback"),
        "strengths": payload.get("strengths"),
        "improvements": payload.get("improvements"),
        "dialogue_coaching": payload.get("dialogue_coaching"),
    }


def validate_payload(
    payload: dict, session_messages: list[dict]
) -> tuple[dict, str]:
    """Validate payload against session messages.

    Checks message_id sanity, quote verbatim, length clamps.
    Returns (validated_payload, status).
    """
    from src.services.session_synthesizer import (
        SessionSynthesizer,
    )

    synth = SessionSynthesizer.__new__(SessionSynthesizer)
    return synth._validate(payload, session_messages)

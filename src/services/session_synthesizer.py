"""Session synthesis service for issue #28.

Generates structured coaching feedback (brief_feedback, strengths,
improvements, dialogue_coaching) from a teacher-student dialogue
session using an LLM call.

Post-validation enforces message_id integrity, length bounds,
and verbatim-quote checks.
"""

import hashlib
import json
import logging
from typing import Any, Optional

from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import config
from src.utils.cache import load_prompt_template
from src.utils.openai_helpers import extract_response_text, extract_usage_dict

logger = logging.getLogger(__name__)

# Re-use the same fallback constant as analysis_pipeline
FALLBACK_FEEDBACK = (
    "분석에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
)

FAILED_PAYLOAD = {
    "version": 1,
    "brief_feedback": [FALLBACK_FEEDBACK],
    "strengths": [],
    "improvements": [],
    "dialogue_coaching": [],
}


def prompt_hash(template_content: str) -> str:
    """SHA-256 hash of the prompt template file contents."""
    return hashlib.sha256(template_content.encode("utf-8")).hexdigest()


class SessionSynthesizer:
    """Synthesize structured coaching feedback for a session.

    Usage::

        synth = SessionSynthesizer()
        payload, status = await synth.synthesize(
            messages=[...],
            question_analyses=[...],
            scenario="분수 덧셈 탐색",
            misconception="분모 통분 불가",
            framework=framework,
            student_profile="초등학교 5학년",
        )
    """

    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.ANALYSIS_MODEL or "gpt-5"
        self.reasoning_effort = config.ANALYSIS_REASONING
        self._template = load_prompt_template("session_synthesis_prompt.txt")
        self._hash = prompt_hash(self._template)
        self.last_usage: dict[str, int] | None = None

    @retry(
        retry=retry_if_exception_type(
            (APIConnectionError, APIError, RateLimitError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def synthesize(
        self,
        messages: list[dict[str, Any]],
        question_analyses: Optional[list[dict]] = None,
        scenario: str = "",
        misconception: str = "",
        student_profile: str = "",
        framework: Optional[Any] = None,
    ) -> tuple[dict[str, Any], str]:
        """Generate structured coaching feedback.

        Args:
            messages: List of message dicts with keys:
                id, role, content.
            question_analyses: Optional per-question classification
                results (label, reasoning).
            scenario: Scenario title.
            misconception: Target misconception text.
            student_profile: Student profile description.
            framework: AnalysisFramework for labels.

        Returns:
            (payload, status) where status is "ok", "degraded",
            or "failed".
        """
        dialogue = self._format_dialogue(messages)
        labels_section = self._format_labels(framework)
        question_analyses_section = self._format_question_analyses(
            question_analyses
        )

        prompt = self._template.format(
            framework_name=getattr(framework, "name", "Unknown"),
            framework_labels_with_criteria=labels_section,
            framework_labels=self._format_label_names(framework),
            question_analyses_section=question_analyses_section,
            scenario_title=scenario,
            misconception=misconception,
            student_profile=student_profile or "Not specified",
            dialogue_transcript=dialogue,
        )

        try:
            response = await self.client.responses.create(
                model=self.model,
                input=[{"role": "user", "content": prompt}],
                max_output_tokens=2500,
                reasoning={"effort": self.reasoning_effort},
            )
            self.last_usage = extract_usage_dict(response)
            content = extract_response_text(response)
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            self.last_usage = None
            logger.error(
                "Synthesis JSON parse failed: %s",
                e,
            )
            return dict(FAILED_PAYLOAD), "failed"
        except (APIConnectionError, RateLimitError, APIError) as e:
            logger.error(
                "Synthesis API error: %s: %s",
                type(e).__name__,
                str(e),
            )
            raise

        if not isinstance(payload, dict):
            logger.error(
                "Synthesis JSON parse failed: payload must be an object"
            )
            return dict(FAILED_PAYLOAD), "failed"

        payload["version"] = 1
        payload, status = self._validate(payload, messages)
        return payload, status

    def _format_dialogue(self, messages: list[dict[str, Any]]) -> str:
        """Format messages as numbered dialogue transcript."""
        role_map = {
            "teacher": "선생님",
            "student": "지수(학생)",
            "tutor": "엔토(멘토)",
        }
        lines = []
        for msg in messages:
            role_label = role_map.get(msg.get("role", ""), msg.get("role", ""))
            lines.append(
                f"[{msg.get('id', '?')}] {role_label}: {msg.get('content', '')}"
            )
        return "\n".join(lines)

    def _format_labels(self, framework: Optional[Any]) -> str:
        """Format framework labels with criteria."""
        if framework is None:
            return "No framework specified"
        criteria_map = getattr(framework, "label_criteria_map", {})
        return "\n".join(
            f"- **{name}**: {criteria}" if criteria else f"- **{name}**"
            for name, criteria in criteria_map.items()
        )

    def _format_label_names(self, framework: Optional[Any]) -> str:
        """Format framework label names as comma-separated list."""
        if framework is None:
            return "N/A"
        names = getattr(framework, "label_names", [])
        return ", ".join(names)

    def _format_question_analyses(
        self,
        question_analyses: Optional[list[dict]],
    ) -> str:
        """Format per-question classification output for synthesis prompt."""
        if not question_analyses:
            return "No per-question analysis results were provided."

        lines = []
        for qa in question_analyses:
            if not isinstance(qa, dict):
                continue
            message_id = qa.get("message_id", "?")
            label = qa.get("label") or "Unclassified"
            confidence = qa.get("confidence")
            reasoning = qa.get("reasoning") or qa.get("meta_json") or ""
            if isinstance(reasoning, str):
                try:
                    parsed = json.loads(reasoning)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    reasoning = parsed
            if isinstance(reasoning, dict):
                reasoning = reasoning.get("summary") or json.dumps(
                    reasoning,
                    ensure_ascii=False,
                )
            parts = [f"- Message {message_id}: {label}"]
            if confidence is not None:
                parts.append(f"(confidence={confidence})")
            if reasoning:
                parts.append(f"— {reasoning}")
            lines.append(" ".join(parts))

        return "\n".join(lines) or (
            "No per-question analysis results were provided."
        )

    def _validate(
        self,
        payload: dict[str, Any],
        session_messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """Post-validate synthesis payload.

        Checks:
        - message_id references exist in session
        - brief_feedback items ≤ 70 Korean chars
        - strengths.quote appears verbatim in message
        - improvements.alternative_question ≤ 60 chars
        - No JSON-looking brief_feedback items

        Returns validated (payload, status).
        """
        if not isinstance(payload, dict):
            logger.warning("Dropping synthesis payload: not a JSON object")
            return dict(FAILED_PAYLOAD), "failed"

        msg_ids = {m.get("id") for m in session_messages}
        msg_roles = {m.get("id"): m.get("role") for m in session_messages}
        msg_content = {
            m.get("id"): m.get("content", "") for m in session_messages
        }
        errors = 0

        # Validate brief_feedback
        bf = payload.get("brief_feedback", [])
        if not isinstance(bf, list):
            bf = []
            errors += 1
        valid_bf = []
        for item in bf:
            if not isinstance(item, str):
                errors += 1
                continue
            stripped = item.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                errors += 1
                continue
            if _korean_char_count(stripped) > 70:
                stripped = _truncate_korean(stripped, 70)
            valid_bf.append(stripped)
        payload["brief_feedback"] = valid_bf

        # Validate strengths
        strengths = payload.get("strengths", [])
        if not isinstance(strengths, list):
            strengths = []
            errors += 1
        valid_strengths = []
        for s in strengths:
            if not isinstance(s, dict):
                errors += 1
                continue
            mid = s.get("message_id")
            if mid not in msg_ids or msg_roles.get(mid) != "teacher":
                errors += 1
                continue
            quote = s.get("quote", "")
            if not isinstance(quote, str) or not quote.strip():
                errors += 1
                continue
            content = msg_content.get(mid, "")
            if quote and content and quote not in content:
                logger.warning(
                    "Dropping strength: quote not verbatim in msg %s",
                    mid,
                )
                errors += 1
                continue
            valid_strengths.append(s)
        payload["strengths"] = valid_strengths

        # Validate improvements
        improvements = payload.get("improvements", [])
        if not isinstance(improvements, list):
            improvements = []
            errors += 1
        valid_improvements = []
        for imp in improvements:
            if not isinstance(imp, dict):
                errors += 1
                continue
            mid = imp.get("student_message_id")
            if mid not in msg_ids or msg_roles.get(mid) != "student":
                errors += 1
                continue
            student_quote = imp.get("student_quote", "")
            content = msg_content.get(mid, "")
            if (
                isinstance(student_quote, str)
                and student_quote.strip()
                and content
                and student_quote not in content
            ):
                errors += 1
                continue
            if not isinstance(student_quote, str):
                errors += 1
                continue
            alt_q = imp.get("alternative_question", "")
            if (
                not isinstance(alt_q, str)
                or not alt_q.strip()
                or _korean_char_count(alt_q) > 60
            ):
                errors += 1
                continue
            valid_improvements.append(imp)
        payload["improvements"] = valid_improvements

        # Validate dialogue_coaching
        coaching = payload.get("dialogue_coaching", [])
        if not isinstance(coaching, list):
            coaching = []
            errors += 1
        valid_coaching = []
        allowed_markers = {"good_moment", "missed_moment", "key_clue"}
        for c in coaching:
            if not isinstance(c, dict):
                errors += 1
                continue
            mid = c.get("message_id")
            if mid not in msg_ids:
                errors += 1
                continue
            role = c.get("role")
            if role != msg_roles.get(mid) or role == "tutor":
                errors += 1
                continue
            if c.get("marker") not in allowed_markers:
                errors += 1
                continue
            valid_coaching.append(c)
        payload["dialogue_coaching"] = valid_coaching

        # Determine status
        has_core = len(valid_bf) >= 1
        has_detail = len(valid_strengths) > 0 or len(valid_improvements) > 0

        if errors == 0 and has_core and has_detail:
            status = "ok"
        elif has_core:
            status = "degraded"
        else:
            status = "failed"
            payload = dict(FAILED_PAYLOAD)

        logger.info(
            "Synthesis validation: status=%s, errors=%d, "
            "bf=%d, str=%d, imp=%d, coach=%d",
            status,
            errors,
            len(valid_bf),
            len(valid_strengths),
            len(valid_improvements),
            len(valid_coaching),
        )
        return payload, status


def _korean_char_count(text: str) -> int:
    """Count effective character length for Korean text.

    Korean characters are wider; use a simple heuristic that
    counts CJK characters as 1 each (same as visual length).
    """
    return len(text)


def _truncate_korean(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, breaking at word boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to break at last space
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        return truncated[:last_space] + "…"
    return truncated + "…"

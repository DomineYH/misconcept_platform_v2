"""Helpers for parsing OpenAI Responses API payloads."""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _coerce_text(candidate: Any) -> str | None:
    """Return stripped text from various structures."""
    if candidate is None:
        return None

    if isinstance(candidate, str):
        stripped = candidate.strip()
        return stripped or None

    if isinstance(candidate, dict):
        value = candidate.get("value") or candidate.get("text")
        return _coerce_text(value)

    value_attr = getattr(candidate, "value", None)
    if isinstance(value_attr, str):
        stripped = value_attr.strip()
        return stripped or None

    return None


def _extract_from_blocks(blocks: Iterable[Any]) -> str | None:
    """Extract text from iterable of output content blocks."""
    if not blocks:
        return None

    for block in blocks:
        text = _coerce_text(getattr(block, "text", None))
        if text:
            return text

        nested = getattr(block, "content", None)
        text = _coerce_text(nested)
        if text:
            return text

    return None


def _debug_response_structure(response: Any, prefix: str = "") -> None:
    """Log response structure for debugging."""
    try:
        attrs = [a for a in dir(response) if not a.startswith("_")]
        logger.debug(f"{prefix}Response attrs: {attrs[:20]}")

        for attr in ["output_text", "output", "content", "text", "choices"]:
            if hasattr(response, attr):
                val = getattr(response, attr)
                val_type = type(val).__name__
                if isinstance(val, str):
                    logger.debug(f"{prefix}{attr}: (str) {val[:100]}...")
                elif isinstance(val, (list, tuple)):
                    logger.debug(f"{prefix}{attr}: ({val_type}) len={len(val)}")
                    if val and len(val) > 0:
                        first = val[0]
                        logger.debug(
                            f"{prefix}  [0]: type={type(first).__name__}"
                        )
                else:
                    logger.debug(f"{prefix}{attr}: ({val_type}) {val}")
    except Exception as e:
        logger.debug(f"{prefix}Debug failed: {e}")


def extract_usage_dict(response: Any) -> dict | None:
    """Extract usage info from OpenAI response.

    Args:
        response: OpenAI API response object

    Returns:
        Dictionary with prompt_tokens, completion_tokens, total_tokens
        or None if usage info not available
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion_tokens = usage.get("completion_tokens") or usage.get(
            "output_tokens"
        )
        total_tokens = usage.get("total_tokens")
    else:
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)

    if not all(
        isinstance(value, int)
        for value in (prompt_tokens, completion_tokens, total_tokens)
    ):
        return None

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def extract_response_text(response: Any) -> str:
    """Extract consolidated text from a Responses API response object.

    For "incomplete" Responses API payloads (e.g., max_output_tokens
    reached), we try to return whatever text was produced instead of
    raising immediately so the caller gets a partial but usable result.
    """

    if response is None:
        raise ValueError("Response object is required")

    # Debug: Log response structure
    logger.debug("Extracting text from Responses API output")
    _debug_response_structure(response, "[extract] ")

    status = getattr(response, "status", None)
    incomplete_details = getattr(response, "incomplete_details", None)
    incomplete_reason = None
    if incomplete_details:
        incomplete_reason = getattr(incomplete_details, "reason", None)
        if not incomplete_reason and isinstance(incomplete_details, dict):
            incomplete_reason = incomplete_details.get("reason")
    is_incomplete = status == "incomplete"

    def _return(text: str) -> str:
        """Return extracted text, logging if the response was incomplete."""
        if is_incomplete:
            logger.warning(
                "Response marked incomplete (reason: %s); returning partial "
                "text. "
                "Consider increasing max_output_tokens for reasoning models.",
                incomplete_reason,
            )
        return text

    # 1. Direct output_text string (most common in newer API)
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        stripped = output_text.strip()
        if stripped:
            logger.debug(f"Extracted via output_text: {stripped[:50]}...")
            return _return(stripped)

    # 2. output_text as iterable of strings
    if isinstance(output_text, Iterable) and not isinstance(
        output_text, (str, bytes)
    ):
        collected = [
            text.strip() for text in output_text if isinstance(text, str)
        ]
        if collected:
            joined = "\n".join(text for text in collected if text)
            if joined:
                logger.debug(f"Extracted via output_text iter: {joined[:50]}")
                return _return(joined)

    output = getattr(response, "output", None)

    # 3. Check for choices (Chat Completions API fallback)
    choices = getattr(response, "choices", None)
    if choices and isinstance(choices, (list, tuple)) and len(choices) > 0:
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message:
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                logger.debug(f"Extracted via choices: {content[:50]}...")
                return _return(content.strip())

    if output is None:
        # Try to extract from response as dict
        if hasattr(response, "model_dump"):
            resp_dict = response.model_dump()
            logger.debug(f"Response dict keys: {resp_dict.keys()}")
            if "output_text" in resp_dict:
                text = resp_dict["output_text"]
                if isinstance(text, str) and text.strip():
                    return _return(text.strip())
        raise ValueError("Response missing output content")

    # 4. Some mocks expose response.output.content directly as string
    direct_content = getattr(output, "content", None)
    if isinstance(direct_content, str):
        stripped = direct_content.strip()
        if not stripped:
            raise ValueError("Empty response content")
        logger.debug(f"Extracted via output.content: {stripped[:50]}...")
        return _return(stripped)

    # 5. The official API returns list of output items with nested blocks
    if isinstance(output, Iterable):
        for idx, item in enumerate(output):
            logger.debug(
                f"Processing output item {idx}: type={type(item).__name__}"
            )
            _debug_response_structure(item, f"  [item {idx}] ")

            # Handle message type items with content blocks
            item_type = getattr(item, "type", None)
            logger.debug(f"  Item type: {item_type}")

            if item_type == "message":
                blocks = getattr(item, "content", None)
                if isinstance(blocks, Iterable):
                    for bidx, block in enumerate(blocks):
                        block_type = getattr(block, "type", None)
                        logger.debug(f"    Block {bidx} type: {block_type}")

                        # Handle output_text type blocks
                        if block_type == "output_text":
                            text_val = getattr(block, "text", None)
                            if isinstance(text_val, str) and text_val.strip():
                                logger.debug(f"Extracted: {text_val[:50]}...")
                                return _return(text_val.strip())

                        # Handle text type blocks (alternative format)
                        if block_type == "text":
                            text_val = getattr(block, "text", None)
                            if isinstance(text_val, str) and text_val.strip():
                                logger.debug(f"Extracted: {text_val[:50]}...")
                                return _return(text_val.strip())

            # Fallback: direct content attribute on item
            blocks = getattr(item, "content", None)
            if isinstance(blocks, str):
                stripped = blocks.strip()
                if stripped:
                    logger.debug(f"Extracted via item.content: {stripped[:50]}")
                    return _return(stripped)

            # Try extracting from blocks
            if isinstance(blocks, Iterable):
                text = _extract_from_blocks(blocks)
                if text:
                    logger.debug(f"Extracted from blocks: {text[:50]}...")
                    return _return(text)

            # Try text attribute directly on item
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str) and item_text.strip():
                logger.debug(f"Extracted via item.text: {item_text[:50]}...")
                return _return(item_text.strip())

    # Final debug dump before failing
    logger.error(f"Failed to extract text. Response type: {type(response)}")
    if hasattr(response, "model_dump"):
        logger.error(f"Response dump: {response.model_dump()}")

    if is_incomplete:
        raise ValueError(
            "Response incomplete and contained no extractable text. "
            "Increase max_output_tokens for GPT-5 reasoning."
        )

    raise ValueError("Unable to extract text from Responses API output")

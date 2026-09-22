"""
Caching utilities for performance optimization (T111).

Provides LRU cache for prompt templates and other reusable data.
"""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def load_prompt_template(template_name: str) -> str:
    """
    Load and cache prompt template from file.

    Args:
        template_name: Name of template file (e.g., 'student_system.txt')

    Returns:
        Template content as string

    Raises:
        FileNotFoundError: If template file doesn't exist
    """
    prompt_path = (
        Path(__file__).parent.parent / "prompts" / template_name
    )

    if not prompt_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")

    return prompt_path.read_text(encoding="utf-8")

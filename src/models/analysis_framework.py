"""AnalysisFramework model for pedagogical taxonomies (T023)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from src.db.connection import Base

if TYPE_CHECKING:
    from src.models.scenario import Scenario


class AnalysisFramework(Base):
    """Framework for classifying teacher questions (e.g., leverage)."""

    __tablename__ = "analysis_framework"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Framework identity
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Labels as JSON array
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    scenarios: Mapped[list["Scenario"]] = relationship(
        "Scenario",
        back_populates="framework",
        passive_deletes=True,
    )

    @property
    def labels(self) -> list:
        """Parse JSON labels (supports both formats).

        Returns list[str] or list[dict] depending on stored format.
        """
        return json.loads(self.labels_json)

    @property
    def label_names(self) -> list[str]:
        """Get just the label names (both formats)."""
        parsed = json.loads(self.labels_json)
        if not parsed:
            return []
        if isinstance(parsed[0], dict):
            return [item["name"] for item in parsed]
        return parsed

    @property
    def label_criteria_map(self) -> dict[str, str]:
        """Map label name to criteria text."""
        parsed = json.loads(self.labels_json)
        if not parsed or isinstance(parsed[0], str):
            return {label: "" for label in parsed}
        return {item["name"]: item.get("criteria", "") for item in parsed}

    @property
    def labels_grade_map(self) -> dict[str, str | None]:
        """Map label name to grade display text based on level.

        high → "우수", low → "개선", else None.
        Handles 3 formats: legacy str list, dict w/o level, dict w/ level.
        """
        parsed = json.loads(self.labels_json)
        result: dict[str, str | None] = {}
        if not parsed:
            return result
        if isinstance(parsed[0], str):
            return {label: None for label in parsed}
        for item in parsed:
            level = item.get("level")
            if level == "high":
                grade = "우수"
            elif level == "low":
                grade = "개선"
            else:
                grade = None
            result[item["name"]] = grade
        return result

    @labels.setter
    def labels(self, value: list) -> None:
        """Convert Python list to JSON string."""
        self.labels_json = json.dumps(value, ensure_ascii=False)

    @validates("labels_json")
    def validate_labels_json(self, key: str, value: str) -> str:
        """Validate JSON array (str or dict items)."""
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("labels_json must be JSON array")
            if not (2 <= len(parsed) <= 20):
                raise ValueError("labels must have 2-20 elements")
            # Validate dict items have "name" key
            for item in parsed:
                if isinstance(item, dict):
                    if "name" not in item:
                        raise ValueError("dict labels must have 'name' key")
                    level = item.get("level")
                    if level is not None and level not in ("high", "low"):
                        raise ValueError(
                            "label level must be 'high' or 'low'"
                        )
            return value
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

    def __repr__(self) -> str:
        return f"<AnalysisFramework(id={self.id}, name={self.name})>"

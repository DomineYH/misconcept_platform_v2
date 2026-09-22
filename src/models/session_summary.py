"""SessionSummary model for aggregated session statistics (T056)."""
from datetime import datetime
from typing import Optional, Dict
import json
from sqlalchemy import (
    ForeignKey,
    Integer,
    Text,
    DateTime,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class SessionSummary(Base):
    """
    Aggregated statistics and feedback for completed dialogue session.

    Stores distribution of question types and LLM-generated feedback
    after session analysis.
    """

    __tablename__ = "session_summary"

    # Primary key
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # Foreign key to Session (unique - one summary per session)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("session.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Distribution JSON (label -> count mapping)
    distribution_json: Mapped[str] = mapped_column(Text, nullable=False)

    # LLM-generated feedback and suggestions
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="summary"
    )

    @property
    def distribution(self) -> Dict[str, int]:
        """Parse distribution_json to dict."""
        return json.loads(self.distribution_json)

    @distribution.setter
    def distribution(self, value: Dict[str, int]) -> None:
        """Set distribution from dict."""
        self.distribution_json = json.dumps(value)

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<SessionSummary(id={self.id}, "
            f"session_id={self.session_id}, "
            f"created_at={self.created_at})>"
        )

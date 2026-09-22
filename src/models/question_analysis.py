"""QuestionAnalysis model for teacher message classification (T055)."""
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class QuestionAnalysis(Base):
    """
    Classification result for teacher messages using analysis framework.

    Stores LLM-generated classification (label), confidence score,
    and optional metadata for evidence and rationale.
    """

    __tablename__ = "question_analysis"

    # Primary key
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # Foreign key to Message (unique - one analysis per message)
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("message.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Classification label (e.g., "high_leverage", "medium_leverage")
    label: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )

    # Grade from framework label level (우수/개선)
    grade: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )

    # Confidence score (0.0-1.0) - optional
    confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    # Metadata JSON (evidence, rationale) - optional
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    message: Mapped["Message"] = relationship(  # noqa: F821
        "Message", back_populates="question_analysis"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "(confidence IS NULL) OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_confidence_range",
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<QuestionAnalysis(id={self.id}, "
            f"message_id={self.message_id}, "
            f"label='{self.label}', "
            f"confidence={self.confidence})>"
        )

"""Message model for dialogue turns (T026)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class Message(Base):
    """Individual dialogue message from teacher, student, or tutor."""

    __tablename__ = "message"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Foreign key
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )

    turn_id: Mapped[str | None] = mapped_column(String(36))
    turn_index: Mapped[int | None] = mapped_column(Integer)
    generation_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generation_run.id", ondelete="SET NULL")
    )

    # Message content
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # teacher, student, tutor
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata (JSON string) for misconception analysis and other metadata
    analysis_metadata: Mapped[Optional[str]] = mapped_column(
        "metadata", Text, nullable=True
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="messages"
    )
    question_analysis: Mapped["QuestionAnalysis"] = relationship(  # noqa: F821
        "QuestionAnalysis",
        back_populates="message",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "role IN ('teacher', 'student', 'tutor')", name="ck_message_role"
        ),
        Index(
            "uq_message_turn_role",
            "session_id",
            "turn_id",
            "role",
            unique=True,
            sqlite_where=text("turn_id IS NOT NULL"),
        ),
        Index(
            "uq_message_teacher_index",
            "session_id",
            "turn_index",
            unique=True,
            sqlite_where=text("role = 'teacher' AND turn_index IS NOT NULL"),
        ),
        Index(
            "uq_message_run",
            "generation_run_id",
            unique=True,
            sqlite_where=text("generation_run_id IS NOT NULL"),
        ),
        Index(
            "ix_message_completed_student",
            "session_id",
            text("turn_index DESC"),
            sqlite_where=text("role = 'student' AND turn_id IS NOT NULL"),
        ),
        Index("ix_message_session_created", "session_id", "created_at"),
        Index("ix_message_session_role", "session_id", "role"),
    )

    def __repr__(self) -> str:
        preview = (
            self.content[:30] + "..."
            if len(self.content) > 30
            else self.content
        )
        return f"<Message(id={self.id}, role={self.role}, content={preview})>"

"""Session model for dialogue instances (T025)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class Session(Base):
    """Teacher-student dialogue session instance."""

    __tablename__ = "session"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Foreign keys
    scenario_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scenario.id", ondelete="CASCADE"),
        nullable=False,
    )
    teacher_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    # TutorBot state persistence
    tutor_intervention_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    tutor_question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Relationships
    scenario: Mapped["Scenario"] = relationship(  # noqa: F821
        "Scenario", back_populates="sessions"
    )
    teacher: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="sessions", foreign_keys=[teacher_id]
    )
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    summary: Mapped["SessionSummary"] = relationship(  # noqa: F821
        "SessionSummary",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    api_usage_logs: Mapped[list["ApiUsageLog"]] = relationship(  # noqa: F821
        "ApiUsageLog",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    feedback_report: Mapped["SessionFeedbackReport"] = (  # noqa: F821
        relationship(
            "SessionFeedbackReport",
            back_populates="session",
            uselist=False,
            cascade="all, delete-orphan",
        )
    )
    ui_events: Mapped[list["UiEvent"]] = relationship(  # noqa: F821
        "UiEvent",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index("ix_session_teacher_started", "teacher_id", "started_at"),
        Index("ix_session_ended", "ended_at"),
        Index("idx_session_deleted", "deleted_at"),
    )

    @property
    def is_active(self) -> bool:
        """Check if session is still active (not ended)."""
        return self.ended_at is None

    def mark_deleted(self) -> None:
        """Mark session as soft-deleted with UTC timestamp."""
        self.deleted_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        if self.deleted_at:
            status = "deleted"
        elif self.is_active:
            status = "active"
        else:
            status = "ended"
        return f"<Session(id={self.id}, status={status})>"

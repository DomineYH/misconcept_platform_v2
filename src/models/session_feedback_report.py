"""SessionFeedbackReport model for structured LLM synthesis output.

Issue #28.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class SessionFeedbackReport(Base):
    """Structured session-level feedback synthesis from LLM.

    Stores the full JSON payload (brief_feedback, strengths, improvements,
    dialogue_coaching) separately from SessionSummary.feedback which remains
    a human-readable one-line sentence consumed by CSV export and admin API.

    One-to-one with Session via unique session_id FK.
    """

    __tablename__ = "session_feedback_report"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("session.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    model: Mapped[str] = mapped_column(String(64), nullable=False)

    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    payload_json: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session",
        back_populates="feedback_report",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'degraded', 'failed')",
            name="ck_session_feedback_report_status",
        ),
        Index(
            "ix_session_feedback_report_session",
            "session_id",
        ),
        Index(
            "ix_session_feedback_report_status",
            "status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SessionFeedbackReport(id={self.id}, "
            f"session_id={self.session_id}, "
            f"status='{self.status}', "
            f"model='{self.model}')>"
        )

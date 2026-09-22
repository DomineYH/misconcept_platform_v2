"""UiEvent model for engagement instrumentation (Issue #28)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class UiEvent(Base):
    """Lightweight UI interaction event for engagement metrics.

    Records user interactions with analysis features (e.g. opening detail view)
    to support phase-2 go/no-go decisions. Semantically separate from
    ApiUsageLog which tracks API costs.
    """

    __tablename__ = "ui_event"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )

    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("session.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship(  # noqa: F821
        "User",
        back_populates="ui_events",
    )
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session",
        back_populates="ui_events",
    )

    __table_args__ = (
        Index("ix_ui_event_session", "session_id"),
        Index("ix_ui_event_event_type", "event_type"),
    )

    def __repr__(self) -> str:
        return (
            f"<UiEvent(id={self.id}, "
            f"user_id={self.user_id}, "
            f"session_id={self.session_id}, "
            f"event_type='{self.event_type}')>"
        )

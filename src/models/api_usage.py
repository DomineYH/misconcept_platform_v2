"""API usage tracking model for OpenAI API calls (Task 3.1.1)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class ApiUsageLog(Base):
    """Track OpenAI API usage for cost and token monitoring.

    Records token usage and estimated cost for each bot API call.
    Supports session-level and bot-level analytics.
    """

    __tablename__ = "api_usage_log"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Foreign key to session
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("session.id"), nullable=True
    )

    # Bot identification
    bot_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # 'student' or 'tutor'

    # Model information
    model: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g., 'gpt-4o', 'gpt-4o-mini'

    # Token usage
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cost tracking (USD)
    estimated_cost_usd: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # Timestamp (timezone-aware UTC)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Operation type for cost tracking (Issue #28)
    # 'classification', 'synthesis', 'greeting'. NULL for pre-#28 rows.
    operation: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )

    # Nullable metadata distinguishes legacy rows from invocation attempts.
    invocation_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_run.id"))
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    attempt_no: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    probe_step: Mapped[str | None] = mapped_column(Text)
    credential_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_output_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    retry_wait_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    raw_usage_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    context_budget_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True)
    )
    pricing_as_of: Mapped[str | None] = mapped_column(Text)
    pricing_source: Mapped[str | None] = mapped_column(Text)
    usage_complete: Mapped[bool | None] = mapped_column()

    # Relationship to session
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="api_usage_logs"
    )

    # Indexes for query optimization
    __table_args__ = (
        UniqueConstraint("invocation_id", "attempt_no"),
        Index("ix_api_usage_session_id", "session_id"),
        Index("ix_api_usage_timestamp", "timestamp"),
        Index("ix_api_usage_bot_type", "bot_type"),
    )

    def __repr__(self) -> str:
        """String representation of API usage log."""
        return (
            f"<ApiUsageLog(id={self.id}, "
            f"session_id={self.session_id}, "
            f"bot={self.bot_type}, "
            f"model={self.model}, "
            f"tokens={self.total_tokens}, "
            f"cost={self.estimated_cost_usd})>"
        )

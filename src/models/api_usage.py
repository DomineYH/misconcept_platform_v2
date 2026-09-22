"""API usage tracking model for OpenAI API calls (Task 3.1.1)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
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
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("session.id"), nullable=False
    )

    # Bot identification
    bot_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'student' or 'tutor'

    # Model information
    model: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., 'gpt-4o', 'gpt-4o-mini'

    # Token usage
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    # Cost tracking (USD)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)

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

    # Relationship to session
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="api_usage_logs"
    )

    # Indexes for query optimization
    __table_args__ = (
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
            f"cost=${self.estimated_cost_usd:.6f})>"
        )


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate estimated API cost in USD.

    Pricing as of 2025-06 (per 1M tokens):
    - gpt-5: $5.00 input, $15.00 output
    - gpt-5-mini: $0.30 input, $1.20 output
    - gpt-5.1: $2.00 input, $8.00 output
    - gpt-5.1-chat-latest: $2.00 input, $8.00 output
    - gpt-5.2: $10.00 input, $30.00 output
    - gpt-4o: $5.00 input, $15.00 output
    - gpt-4o-mini: $0.15 input, $0.60 output
    - gpt-4-turbo: $10.00 input, $30.00 output
    - gpt-3.5-turbo: $0.50 input, $1.50 output

    Args:
        model: OpenAI model name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens

    Returns:
        Estimated cost in USD (6 decimal places)
    """
    # Pricing table (USD per 1M tokens)
    pricing_table = {
        # GPT-5 family (pricing as of 2025-06)
        "gpt-5": {"input": 5.00, "output": 15.00},
        "gpt-5-mini": {"input": 0.30, "output": 1.20},
        "gpt-5.1": {"input": 2.00, "output": 8.00},
        "gpt-5.1-chat-latest": {"input": 2.00, "output": 8.00},
        "gpt-5.2": {"input": 10.00, "output": 30.00},
        # GPT-4 family
        "gpt-4o": {"input": 5.00, "output": 15.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        # GPT-3.5 family (Legacy: kept for historical records)
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    }

    # Default to gpt-5-mini pricing if model not found
    pricing = pricing_table.get(model, pricing_table["gpt-5-mini"])

    # Calculate cost (tokens / 1M * price per 1M)
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 6)

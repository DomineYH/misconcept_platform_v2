"""One saved-scenario comparison, with immutable setup and adopted dialogue."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


class ComparisonConversation(Base):
    __tablename__ = "comparison_conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario.id", ondelete="SET NULL")
    )
    source_version: Mapped[int] = mapped_column(Integer)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    panel_configs: Mapped[list] = mapped_column(JSON)
    state_json: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint(
            "admin_id", "request_id", name="uq_conversation_request"
        ),
        CheckConstraint("version >= 1"),
    )

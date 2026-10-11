"""Frozen administrator comparisons, independent of classroom sessions."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


class ComparisonRun(Base):
    __tablename__ = "comparison_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario.id", ondelete="SET NULL")
    )
    source_version: Mapped[int | None] = mapped_column(Integer)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    target_role: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    canonical_context: Mapped[list] = mapped_column(JSON)
    current_message: Mapped[str | None] = mapped_column(Text)
    context_hash: Mapped[str] = mapped_column(String(64))
    panel_configs: Mapped[list] = mapped_column(JSON)
    request_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("comparison_run.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint(
            "admin_id", "request_id", name="uq_comparison_request"
        ),
        CheckConstraint("target_role IN ('student','mentor','analysis')"),
        CheckConstraint("mode = 'same_context'"),
        CheckConstraint("status IN ('running','finished')"),
        Index(
            "uq_comparison_running_admin",
            "admin_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )

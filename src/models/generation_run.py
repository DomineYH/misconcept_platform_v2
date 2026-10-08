"""Durable generation attempts; teacher messages anchor turn identity."""

from datetime import datetime, timezone

from sqlalchemy import (
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


class GenerationRun(Base):
    __tablename__ = "generation_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="SET NULL")
    )
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("session.id", ondelete="CASCADE")
    )
    turn_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    partial_text: Mapped[str | None] = mapped_column(Text)
    result_kind: Mapped[str | None] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    first_output_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "session_id", "request_id", name="uq_run_request"
        ),
        CheckConstraint("operation IN ('student','mentor')"),
        CheckConstraint("provider IN ('openai','anthropic','google')"),
        CheckConstraint(
            "status IN ('running','completed','failed',"
            "'cancelled','interrupted')"
        ),
        CheckConstraint("result_kind IN ('message','no_intervention')"),
        Index(
            "uq_run_running_student",
            "session_id",
            unique=True,
            sqlite_where=text("operation = 'student' AND status = 'running'"),
        ),
        Index(
            "uq_run_running_mentor",
            "session_id",
            unique=True,
            sqlite_where=text("operation = 'mentor' AND status = 'running'"),
        ),
        Index(
            "uq_run_completed_turn",
            "session_id",
            "turn_id",
            "operation",
            unique=True,
            sqlite_where=text("status = 'completed'"),
        ),
        Index("ix_run_turn", "session_id", "turn_id", "operation"),
    )

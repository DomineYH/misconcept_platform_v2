"""Scenario model for dialogue situations (T024)."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base

if TYPE_CHECKING:
    from src.models.scenario_group import ScenarioGroup
    from src.models.session import Session
    from src.models.user import User


class Scenario(Base):
    """Unified scenario; config_json is the only educational configuration."""

    __tablename__ = "scenario"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Scenario identity
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="과목명 (채팅 UI 표시용)",
    )

    # Status
    is_active: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )  # Boolean as int

    target_grade: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default="draft")
    config_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[dict] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    conversion_provenance_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Foreign keys
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    # Relationships
    creator: Mapped["User"] = relationship("User", back_populates="scenarios")
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="scenario",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Scenario-group access control
    scenario_groups: Mapped[list["ScenarioGroup"]] = relationship(
        "ScenarioGroup",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')"),
        CheckConstraint("config_schema_version = 1"),
        CheckConstraint("config_version >= 1"),
        CheckConstraint("is_active IN (0, 1)", name="ck_scenario_active"),
    )

    def __repr__(self) -> str:
        return f"<Scenario(id={self.id}, title={self.title[:30]}, status={self.status})>"

    def mark_deleted(self) -> None:
        """Mark scenario as soft-deleted with UTC timestamp."""
        self.deleted_at = datetime.now(timezone.utc)

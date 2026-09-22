"""ScenarioGroup join table for scenario-group access control."""
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class ScenarioGroup(Base):
    """Join table linking scenarios to user groups."""

    __tablename__ = "scenario_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scenario.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_group.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    scenario: Mapped["Scenario"] = relationship(
        "Scenario", back_populates="scenario_groups"
    )
    group: Mapped["UserGroup"] = relationship(
        "UserGroup", back_populates="scenario_groups"
    )

    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "group_id", name="uq_scenario_group"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ScenarioGroup(scenario={self.scenario_id}, "
            f"group={self.group_id})>"
        )

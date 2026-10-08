"""UserGroup model for organizing users into groups."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base

if TYPE_CHECKING:
    from src.models.scenario_group import ScenarioGroup
    from src.models.user import User


class UserGroup(Base):
    """Group entity for organizing users."""

    __tablename__ = "user_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="group")
    scenario_groups: Mapped[list["ScenarioGroup"]] = relationship(
        "ScenarioGroup", back_populates="group"
    )

    def __repr__(self) -> str:
        return f"<UserGroup(id={self.id}, name={self.name})>"

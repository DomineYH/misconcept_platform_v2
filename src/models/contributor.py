"""Contributor model for developer/maintainer info (About page)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


class Contributor(Base):
    """Developer or maintainer entry for the About page."""

    __tablename__ = "contributor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Required fields
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    affiliation: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    bio: Mapped[str] = mapped_column(String(2000), nullable=False)

    # Optional contact fields
    phone: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    # Display ordering
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<Contributor(id={self.id}, name={self.name}, "
            f"affiliation={self.affiliation})>"
        )

"""User model for teachers and admins (T022)."""

from datetime import datetime, timezone
from typing import Optional

import bcrypt
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base


class User(Base):
    """User entity with ID/password authentication."""

    __tablename__ = "user"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Login credentials
    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    nickname: Mapped[str] = mapped_column(String(30), nullable=False)
    password_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )

    # Role enum
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="teacher"
    )

    # Group assignment
    group_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user_group.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    group: Mapped[Optional["UserGroup"]] = relationship(  # noqa: F821
        "UserGroup", back_populates="users"
    )
    sessions: Mapped[list["Session"]] = relationship(  # noqa: F821
        "Session",
        back_populates="teacher",
        foreign_keys="Session.teacher_id",
    )
    scenarios: Mapped[list["Scenario"]] = relationship(  # noqa: F821
        "Scenario", back_populates="creator"
    )
    prompt_updates: Mapped[list["PromptTemplate"]] = relationship(  # noqa: F821
        "PromptTemplate", back_populates="updater"
    )
    ui_events: Mapped[list["UiEvent"]] = relationship(  # noqa: F821
        "UiEvent",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "role IN ('teacher', 'admin')",
            name="ck_user_role",
        ),
    )

    def set_password(self, plain: str) -> None:
        """Hash and store password."""
        pw_bytes = plain.encode("utf-8")
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(pw_bytes, salt).decode("utf-8")

    def verify_password(self, plain: str) -> bool:
        """Verify password against stored hash."""
        if not self.password_hash:
            return False
        pw_bytes = plain.encode("utf-8")
        hash_bytes = self.password_hash.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hash_bytes)

    def __repr__(self) -> str:
        return (
            f"<User(id={self.id}, "
            f"username={self.username}, "
            f"role={self.role})>"
        )

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == "admin"

    @property
    def is_teacher(self) -> bool:
        """Check if user has teacher role."""
        return self.role == "teacher"

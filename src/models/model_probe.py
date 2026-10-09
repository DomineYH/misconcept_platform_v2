"""Durable, owner-scoped identities for explicit role probes."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


class ModelProbe(Base):
    __tablename__ = "model_probe"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT")
    )
    model_config_id: Mapped[int] = mapped_column(ForeignKey("model_config.id"))
    role: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    credential_revision: Mapped[int] = mapped_column(Integer)
    connection_version: Mapped[int] = mapped_column(Integer)
    config_version: Mapped[int] = mapped_column(Integer)
    capability_definition_version: Mapped[str] = mapped_column(Text)
    role_contract_version: Mapped[str] = mapped_column(Text)
    options_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        Index(
            "ix_probe_active_role",
            "model_config_id",
            "role",
            unique=True,
            sqlite_where=text("status='verifying'"),
        ),
    )

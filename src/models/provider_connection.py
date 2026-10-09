"""Encrypted provider credentials and secret-free change history."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


def now():
    return datetime.now(timezone.utc)


class ProviderConnection(Base):
    __tablename__ = "provider_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), unique=True)
    encrypted_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    encryption_key_version: Mapped[str | None] = mapped_column(String(64))
    credential_revision: Mapped[int] = mapped_column(Integer, default=0)
    connection_version: Mapped[int] = mapped_column(Integer, default=1)
    masked_hint: Mapped[str | None] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(50))
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    catalog_models_json: Mapped[list] = mapped_column(JSON, default=list)
    catalog_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    catalog_credential_revision: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("provider IN ('openai','anthropic','google')"),
        CheckConstraint("credential_revision >= 0 AND connection_version >= 1"),
        CheckConstraint(
            "(encrypted_key IS NULL AND nonce IS NULL AND encryption_key_version IS NULL AND masked_hint IS NULL AND enabled = 0) OR (encrypted_key IS NOT NULL AND length(encrypted_key) >= 17 AND nonce IS NOT NULL AND length(nonce) = 12 AND encryption_key_version IS NOT NULL AND length(encryption_key_version) > 0 AND masked_hint IS NOT NULL)",
            name="ck_provider_key_material",
        ),
    )


class ProviderAuditLog(Base):
    __tablename__ = "provider_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    provider_connection_id: Mapped[int] = mapped_column(
        ForeignKey("provider_connection.id")
    )
    provider: Mapped[str] = mapped_column(String(20))
    change_kind: Mapped[str] = mapped_column(String(20))
    previous_credential_revision: Mapped[int] = mapped_column(Integer)
    credential_revision: Mapped[int] = mapped_column(Integer)
    previous_connection_version: Mapped[int] = mapped_column(Integer)
    connection_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    __table_args__ = (
        CheckConstraint("provider IN ('openai','anthropic','google')"),
        CheckConstraint(
            "change_kind IN ('key_saved','key_replaced','enabled','disabled','deleted')"
        ),
    )

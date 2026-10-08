"""Registered model configuration and singleton authoring/call settings."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base
from src.models.provider_connection import now

ROLES = ("student", "mentor", "analysis")


def unverified_roles():
    return {role: {"status": "unverified"} for role in ROLES}


class ModelConfig(Base):
    __tablename__ = "model_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_connection_id: Mapped[int] = mapped_column(
        ForeignKey("provider_connection.id")
    )
    model_id: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True)
    )
    capability_definition_version: Mapped[str | None] = mapped_column(Text)
    default_options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    verification_state: Mapped[dict] = mapped_column(
        JSON, default=unverified_roles
    )
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    __table_args__ = (
        UniqueConstraint("provider_connection_id", "model_id"),
        CheckConstraint("config_version >= 1"),
    )


class AppSetting(Base):
    __tablename__ = "app_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_version: Mapped[int] = mapped_column(Integer, default=1)
    student_model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_config.id")
    )
    mentor_model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_config.id")
    )
    analysis_model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_config.id")
    )
    limits_json: Mapped[dict] = mapped_column(JSON)
    timeouts_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    __table_args__ = (CheckConstraint("id = 1 AND settings_version >= 1"),)

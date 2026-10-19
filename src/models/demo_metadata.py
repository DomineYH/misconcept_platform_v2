"""Singleton marker identifying an explicitly built seed or visitor clone."""

from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.connection import Base


class DemoMetadata(Base):
    __tablename__ = "demo_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    contract_version: Mapped[str] = mapped_column(Text)
    content_version: Mapped[str] = mapped_column(Text)
    fixture_version: Mapped[str] = mapped_column(Text)
    db_schema_version: Mapped[str] = mapped_column(Text)
    manifest_sha256: Mapped[str] = mapped_column(Text)
    visitor_id: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("id = 1"),
        CheckConstraint("kind IN ('demo_seed','demo_visitor')"),
        CheckConstraint(
            "length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'"
        ),
        CheckConstraint(
            "(kind = 'demo_seed' AND visitor_id IS NULL AND generation IS NULL) OR "
            "(kind = 'demo_visitor' AND visitor_id IS NOT NULL AND length(visitor_id) > 0 AND "
            "generation IS NOT NULL AND typeof(generation) = 'integer' AND generation >= 1)"
        ),
    )

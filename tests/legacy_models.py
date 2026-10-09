"""ORM fixtures for the immutable pre-contract schema, never runtime models."""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, validates

from src.models import User, UserGroup

Base = declarative_base()
UserGroup.__table__.to_metadata(Base.metadata)
User.__table__.to_metadata(Base.metadata)


class AnalysisFramework(Base):
    """Framework for classifying teacher questions (e.g., leverage)."""

    __tablename__ = "analysis_framework"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    @property
    def labels(self) -> list:
        """Parse JSON labels (supports both formats).

        Returns list[str] or list[dict] depending on stored format.
        """
        return json.loads(self.labels_json)

    @property
    def label_names(self) -> list[str]:
        """Get just the label names (both formats)."""
        parsed = json.loads(self.labels_json)
        if not parsed:
            return []
        if isinstance(parsed[0], dict):
            return [item["name"] for item in parsed]
        return parsed

    @property
    def label_criteria_map(self) -> dict[str, str]:
        """Map label name to criteria text."""
        parsed = json.loads(self.labels_json)
        if not parsed or isinstance(parsed[0], str):
            return {label: "" for label in parsed}
        return {item["name"]: item.get("criteria", "") for item in parsed}

    @property
    def labels_grade_map(self) -> dict[str, str | None]:
        """Map label name to grade display text based on level.

        high → "우수", low → "개선", else None.
        Handles 3 formats: legacy str list, dict w/o level, dict w/ level.
        """
        parsed = json.loads(self.labels_json)
        result: dict[str, str | None] = {}
        if not parsed:
            return result
        if isinstance(parsed[0], str):
            return {label: None for label in parsed}
        for item in parsed:
            level = item.get("level")
            if level == "high":
                grade = "우수"
            elif level == "low":
                grade = "개선"
            else:
                grade = None
            result[item["name"]] = grade
        return result

    @labels.setter
    def labels(self, value: list) -> None:
        """Convert Python list to JSON string."""
        self.labels_json = json.dumps(value, ensure_ascii=False)

    @validates("labels_json")
    def validate_labels_json(self, key: str, value: str) -> str:
        """Validate JSON array (str or dict items)."""
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("labels_json must be JSON array")
            if not 2 <= len(parsed) <= 20:
                raise ValueError("labels must have 2-20 elements")
            for item in parsed:
                if isinstance(item, dict):
                    if "name" not in item:
                        raise ValueError("dict labels must have 'name' key")
                    level = item.get("level")
                    if level is not None and level not in ("high", "low"):
                        raise ValueError("label level must be 'high' or 'low'")
            return value
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

    def __repr__(self) -> str:
        return f"<AnalysisFramework(id={self.id}, name={self.name})>"


class PromptTemplate(Base):
    """
    프롬프트 템플릿 엔티티.

    시나리오별로 템플릿을 선택하여 사용합니다.
    버전 히스토리를 통해 이전 프롬프트로 롤백할 수 있습니다.
    """

    __tablename__ = "prompt_template"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_type: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint(
            "bot_type IN ('student', 'tutor')", name="ck_prompt_bot_type"
        ),
        nullable=False,
        comment="봇 타입: student 또는 tutor",
    )
    template_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="템플릿 이름 (예: Default, Spanish Tutor)",
    )
    template_text: Mapped[str] = mapped_column(
        Text,
        CheckConstraint(
            "LENGTH(template_text) >= 10 AND LENGTH(template_text) <= 10000",
            name="ck_prompt_text_length",
        ),
        nullable=False,
        comment="프롬프트 전문 (10-10,000자)",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="버전 번호"
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="생성 시각 (UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="수정 시각 (UTC)",
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user.id"),
        nullable=True,
        comment="수정한 관리자 ID",
    )
    __table_args__ = (
        Index("ix_prompt_bot_type", "bot_type"),
        Index("ix_prompt_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        """객체 문자열 표현."""
        return f"<PromptTemplate(id={self.id}, bot_type='{self.bot_type}', name='{self.template_name}', version={self.version})>"

    def to_dict(self) -> dict:
        """딕셔너리로 변환 (API 응답용)."""
        return {
            "id": self.id,
            "bot_type": self.bot_type,
            "template_name": self.template_name,
            "template_text": self.template_text,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "updated_by": self.updated_by,
        }


class Scenario(Base):
    """Unified scenario; legacy columns remain only for private conversion."""

    __tablename__ = "scenario"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="학생 캐릭터 이름 (채팅 UI 표시용)"
    )
    subject: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="과목명 (채팅 UI 표시용)"
    )
    problem_situation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="예비교사에게 노출되는 문제 상황 텍스트 (시스템 프롬프트와 분리)",
    )
    greeting_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="채팅 시작 시 멘토 안내 메시지"
    )
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_grade: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default="published")
    config_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    conversion_provenance_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    chat_model: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Override StudentBot model for this scenario (NULL = use global)",
    )
    chat_temperature: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Override temperature 0.0-2.0 (NULL = use global)",
    )
    tutor_intervention_threshold: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Override tutor interventions per 10 questions (NULL = use global)",
    )
    tutor_sensitivity: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="medium",
        comment="Tutor intervention sensitivity: high, medium, low",
    )
    student_template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("prompt_template.id", ondelete="SET NULL"),
        nullable=True,
        comment="StudentBot prompt template for this scenario",
    )
    tutor_template_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("prompt_template.id", ondelete="SET NULL"),
        nullable=True,
        comment="TutorBot prompt template (NULL = tutor disabled)",
    )
    framework_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("analysis_framework.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')"),
        CheckConstraint("config_schema_version = 1"),
        CheckConstraint("config_version >= 1"),
        CheckConstraint("is_active IN (0, 1)", name="ck_scenario_active"),
        CheckConstraint(
            "tutor_sensitivity IN ('high', 'medium', 'low')",
            name="ck_scenario_sensitivity",
        ),
    )

    def __repr__(self) -> str:
        return f"<Scenario(id={self.id}, title={self.title[:30]}, status={self.status})>"

    def mark_deleted(self) -> None:
        """Mark scenario as soft-deleted with UTC timestamp."""
        self.deleted_at = datetime.now(timezone.utc)


def native_writer_defaults(database):
    """Keep pre-contract fixtures writable by the final mapper, which has no legacy columns."""
    import re
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(database)) as db:
        columns = db.execute("PRAGMA table_info(scenario)").fetchall()
        sensitivity = next(
            (row for row in columns if row[1] == "tutor_sensitivity"), None
        )
        if sensitivity is None or sensitivity[4] is not None:
            return
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenario'"
        ).fetchone()[0]
        sql = re.sub(
            r"CREATE TABLE [^(]+",
            "CREATE TABLE scenario_fixture ",
            sql,
            count=1,
        )
        sql = sql.replace(
            "tutor_sensitivity VARCHAR(10) NOT NULL",
            "tutor_sensitivity VARCHAR(10) NOT NULL DEFAULT 'medium'",
        )
        indexes = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='scenario' AND sql IS NOT NULL"
        ).fetchall()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        db.execute(sql)
        db.execute("INSERT INTO scenario_fixture SELECT * FROM scenario")
        db.execute("DROP TABLE scenario")
        db.execute("ALTER TABLE scenario_fixture RENAME TO scenario")
        for (index,) in indexes:
            db.execute(index)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.commit()

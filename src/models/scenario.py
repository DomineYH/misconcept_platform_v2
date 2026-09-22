"""Scenario model for dialogue situations (T024)."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base

if TYPE_CHECKING:
    from src.models.analysis_framework import AnalysisFramework
    from src.models.prompt_template import PromptTemplate
    from src.models.scenario_group import ScenarioGroup
    from src.models.session import Session
    from src.models.user import User


class Scenario(Base):
    """Dialogue scenario with misconception and problem context.

    Phase 2 Extension: Supports per-scenario chatbot configuration override.
    NULL values in chat_* and tutor_* fields mean "use global config".
    """

    __tablename__ = "scenario"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Scenario identity
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)  # System prompt
    student_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_name: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="학생 캐릭터 이름 (채팅 UI 표시용)",
    )
    subject: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="과목명 (채팅 UI 표시용)",
    )
    problem_situation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "예비교사에게 노출되는 문제 상황 텍스트 " "(시스템 프롬프트와 분리)"
        ),
    )
    greeting_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="채팅 시작 시 멘토 안내 메시지",
    )

    # Video fields
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    is_active: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )  # Boolean as int

    # Phase 2: Scenario-specific chatbot configuration override
    chat_model: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "Override StudentBot model for this scenario " "(NULL = use global)"
        ),
    )

    chat_temperature: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Override temperature 0.0-2.0 (NULL = use global)",
    )

    tutor_intervention_threshold: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Override tutor interventions per 10 questions "
            "(NULL = use global)"
        ),
    )

    tutor_sensitivity: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="medium",
        comment="Tutor intervention sensitivity: high, medium, low",
    )

    # Template foreign keys
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

    # Foreign keys
    framework_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "analysis_framework.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
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
    framework: Mapped["AnalysisFramework"] = relationship(
        "AnalysisFramework", back_populates="scenarios"
    )
    creator: Mapped["User"] = relationship("User", back_populates="scenarios")
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="scenario",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    student_template: Mapped["PromptTemplate"] = relationship(
        "PromptTemplate",
        foreign_keys=[student_template_id],
        lazy="joined",
    )
    tutor_template: Mapped[Optional["PromptTemplate"]] = relationship(
        "PromptTemplate",
        foreign_keys=[tutor_template_id],
        lazy="joined",
    )

    # Scenario-group access control
    scenario_groups: Mapped[list["ScenarioGroup"]] = relationship(
        "ScenarioGroup",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_scenario_active"),
        CheckConstraint(
            "tutor_sensitivity IN ('high', 'medium', 'low')",
            name="ck_scenario_sensitivity",
        ),
    )

    @property
    def tutor_enabled(self) -> bool:
        """Backward compatibility: tutor enabled if template is assigned."""
        return self.tutor_template_id is not None

    def __repr__(self) -> str:
        config = (
            f"model={self.chat_model or 'global'}, "
            f"tutor={'on' if self.tutor_enabled else 'off'}"
        )
        status = "deleted" if self.deleted_at else "active"
        return (
            f"<Scenario(id={self.id}, "
            f"title={self.title[:30]}, "
            f"{config}, {status})>"
        )

    def mark_deleted(self) -> None:
        """Mark scenario as soft-deleted with UTC timestamp."""
        self.deleted_at = datetime.now(timezone.utc)

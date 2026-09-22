"""
프롬프트 템플릿 모델 (Task 3.2.1).

관리자가 StudentBot과 TutorBot의 시스템 프롬프트를 웹 UI를 통해
관리하고 버전 관리할 수 있는 데이터베이스 모델입니다.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.connection import Base

if TYPE_CHECKING:
    from src.models.user import User


class PromptTemplate(Base):
    """
    프롬프트 템플릿 엔티티.

    시나리오별로 템플릿을 선택하여 사용합니다.
    버전 히스토리를 통해 이전 프롬프트로 롤백할 수 있습니다.
    """

    __tablename__ = "prompt_template"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Bot type: 'student' or 'tutor'
    bot_type: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint(
            "bot_type IN ('student', 'tutor')", name="ck_prompt_bot_type"
        ),
        nullable=False,
        comment="봇 타입: student 또는 tutor",
    )

    # Template metadata
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

    # Version tracking
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="버전 번호"
    )

    # Timestamps
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

    # Foreign key to user
    updated_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user.id"),
        nullable=True,
        comment="수정한 관리자 ID",
    )

    # Relationships
    updater: Mapped["User"] = relationship(
        "User", back_populates="prompt_updates", foreign_keys=[updated_by]
    )

    # Table constraints and indexes
    __table_args__ = (
        # 조회 최적화 인덱스
        Index("ix_prompt_bot_type", "bot_type"),
        Index("ix_prompt_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        """객체 문자열 표현."""
        return (
            f"<PromptTemplate("
            f"id={self.id}, "
            f"bot_type='{self.bot_type}', "
            f"name='{self.template_name}', "
            f"version={self.version}"
            f")>"
        )

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

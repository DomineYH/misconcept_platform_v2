"""Pydantic schemas for analysis API responses.

Slim reasoning model: a free-form summary plus an optional improved
teacher question for `level=low` labels.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DetailedReasoning(BaseModel):
    """질문 분류에 대한 분석 이유."""

    summary: str = Field(
        ..., description="분류 요약 (1-2문장, 프레임워크 기준 포함)"
    )
    improved_sentence: Optional[str] = Field(
        None,
        description="개선한 문장 (low 등급일 때만 채움)",
    )


class QuestionAnalysisResponse(BaseModel):
    """단일 질문 분석 응답."""

    content: str = Field(..., description="교사 질문 내용")
    label: str = Field(..., description="분류 레이블")
    confidence: Optional[float] = Field(None, description="신뢰도 (0.0-1.0)")
    reasoning: Optional[DetailedReasoning] = Field(
        None, description="상세 분석 이유"
    )
    created_at: str = Field(..., description="생성 시간")


class SessionAnalysisResponse(BaseModel):
    """세션 분석 응답."""

    distribution: dict[str, int] = Field(..., description="레이블별 분포")
    feedback: Optional[str] = Field(None, description="전체 피드백")
    questions: list[QuestionAnalysisResponse] = Field(
        default_factory=list, description="질문별 분석 목록"
    )
    session_ended_at: str = Field(..., description="세션 종료 시간")


class AnalysisListItem(BaseModel):
    """관리자 페이지용 분석 목록 항목."""

    id: int
    content: str
    label: str
    confidence: float
    reasoning: Optional[DetailedReasoning] = None
    session_id: int
    scenario_title: str
    created_at: datetime

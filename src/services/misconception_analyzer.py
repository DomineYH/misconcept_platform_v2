"""MisconceptionAnalyzer service for tracking student misconception adherence."""

import json
import logging
from typing import Optional

from openai import APIConnectionError, APIError, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import config
from src.services.base import OpenAIBaseService, openai_retry
from src.utils.openai_helpers import extract_response_text

logger = logging.getLogger(__name__)


class MisconceptionAnalyzer(OpenAIBaseService):
    """실시간 오개념 탐지 및 추적 서비스."""

    def __init__(
        self,
        db_session: AsyncSession,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ):
        """Initialize MisconceptionAnalyzer.

        Args:
            db_session: Database session for future use
            model: Override default model (from config)
            reasoning_effort: Override reasoning effort (minimal, low,
                medium, high)
        """
        super().__init__()
        self.db_session = db_session
        self.model = model or config.ANALYSIS_MODEL
        self.reasoning_effort = (
            reasoning_effort or config.ANALYSIS_REASONING
        )

    @openai_retry
    async def analyze_student_response(
        self,
        student_message: str,
        scenario_prompt: str,
        student_profile: str,
        scenario_title: str,
    ) -> dict:
        """학생 응답이 오개념을 유지하는지 분석.

        Args:
            student_message: 학생의 최신 응답 메시지
            scenario_prompt: 시나리오에 정의된 오개념 프롬프트
            student_profile: 학생 페르소나 및 상황
            scenario_title: 시나리오 제목

        Returns:
            dict: {
                "maintains_misconception": bool,
                "misconception_strength": float (0.0-1.0),
                "evidence": str,
                "drift_detected": bool,
                "analysis_notes": str
            }

        Raises:
            APIError: If OpenAI API fails after retries
        """
        try:
            # 분석 프롬프트 구성
            system_prompt = self._build_analysis_prompt(
                scenario_prompt, student_profile, scenario_title
            )

            # Build input for Responses API (developer role)
            input_messages = [
                {"role": "developer", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Student Response: {student_message}",
                },
            ]

            # OpenAI Responses API 호출 (GPT-5 with reasoning)
            # Note: GPT-5 reasoning consumes tokens from max_output_tokens
            # 500 = ~200 reasoning + ~300 actual output
            response = await self.client.responses.create(
                model=self.model,
                input=input_messages,
                max_output_tokens=500,
                reasoning={"effort": self.reasoning_effort},
            )

            # 응답 파싱 (Responses API output 처리)
            content = extract_response_text(response)
            analysis_result = self._parse_analysis_response(content)

            logger.info(
                f"Misconception analysis: maintains={analysis_result['maintains_misconception']}, "
                f"strength={analysis_result['misconception_strength']:.2f}"
            )

            return analysis_result

        except (APIConnectionError, RateLimitError, APIError) as e:
            logger.error(
                "MisconceptionAnalyzer API error: %s: %s", type(e).__name__, str(e)
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error in MisconceptionAnalyzer: %s", str(e)
            )
            raise Exception(f"Misconception analysis failed: {str(e)}")

    def _build_analysis_prompt(
        self, scenario_prompt: str, student_profile: str, scenario_title: str
    ) -> str:
        """오개념 분석을 위한 시스템 프롬프트 구성."""
        return f"""You are an expert educator analyzing whether a student's response maintains their misconception.

SCENARIO CONTEXT:
- Title: {scenario_title}
- Student Misconception: {scenario_prompt}
- Student Profile: {student_profile}

ANALYSIS TASK:
Analyze the student's response to determine:
1. Does the response maintain the misconception defined above?
2. How strongly is the misconception expressed (0.0 = completely abandoned, 1.0 = fully maintained)?
3. What evidence supports your assessment?
4. Has the student drifted away from their assigned misconception?

RESPONSE FORMAT (JSON):
{{
  "maintains_misconception": true/false,
  "misconception_strength": 0.0-1.0,
  "evidence": "brief explanation of your assessment",
  "drift_detected": true/false,
  "analysis_notes": "additional observations"
}}

Respond ONLY with valid JSON matching the format above."""

    def _parse_analysis_response(self, content: str) -> dict:
        """LLM 응답을 파싱하여 구조화된 분석 결과 반환."""
        try:
            # JSON 추출 시도
            # LLM이 코드 블록으로 감쌌을 수도 있으므로 처리
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            result = json.loads(content)

            # 필수 필드 검증 및 기본값 설정
            return {
                "maintains_misconception": result.get(
                    "maintains_misconception", True
                ),
                "misconception_strength": float(
                    result.get("misconception_strength", 0.5)
                ),
                "evidence": result.get("evidence", "No evidence provided"),
                "drift_detected": result.get("drift_detected", False),
                "analysis_notes": result.get(
                    "analysis_notes", "No additional notes"
                ),
            }

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Failed to parse misconception analysis response: %s", e
            )
            # 파싱 실패 시 기본값 반환
            return {
                "maintains_misconception": True,
                "misconception_strength": 0.5,
                "evidence": f"Parse error: {str(e)}",
                "drift_detected": False,
                "analysis_notes": content[:200],  # 원본 내용 일부 저장
            }

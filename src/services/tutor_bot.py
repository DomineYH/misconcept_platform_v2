"""TutorBot service for pedagogical feedback and intervention."""

import asyncio
import logging
from contextlib import aclosing
from typing import Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import config
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.dialogue_analysis import (
    SENSITIVITY_PRESETS,
    check_low_leverage_patterns,
    check_vague_patterns,
    detect_repetitive_dialogue_simple,
    extract_recent_pairs,
)
from src.services.invocation_types import (
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.lesson_connections import resolve_lesson_model
from src.services.prompt_manager import PromptManager
from src.services.role_output_contracts import LessonInterventionJudgment

logger = logging.getLogger(__name__)


def advance_question_count(question_count, intervention_count):
    question_count += 1
    return (
        (0, 0) if question_count > 10 else (question_count, intervention_count)
    )


class TutorBot:
    """Chatbot providing real-time pedagogical feedback."""

    def __init__(
        self,
        db_session: AsyncSession,
        template_id: int,
        scenario_title: str = "",
        prompt: str = "",
        student_profile: str = "",
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        intervention_threshold: Optional[int] = None,
        initial_intervention_count: int = 0,
        initial_question_count: int = 0,
        sensitivity: str = "medium",
        *,
        factory=None,
        owner_id=None,
        session_id=None,
        run_id=None,
        request_id=None,
        permit=None,
        judgment_model=None,
    ):
        """Initialize TutorBot with scenario context and optional config.

        Args:
            db_session: Database session for queries
            template_id: ID of the tutor prompt template to use
            scenario_title: Title of the scenario
            prompt: Scenario prompt text
            student_profile: Student profile description
            model: OpenAI model to use (defaults to config.ANALYSIS_MODEL)
            reasoning_effort: Reasoning effort level
            max_tokens: Maximum tokens for response
            intervention_threshold: Max interventions per session
            initial_intervention_count: Restored count from prior session
            initial_question_count: Restored count from prior session
            sensitivity: Tutor sensitivity level (high/medium/low)
        """
        self.factory = factory or async_sessionmaker(
            db_session.bind, expire_on_commit=False, autoflush=False
        )
        self.owner_id, self.session_id = owner_id, session_id
        self.run_id, self.request_id = run_id, request_id
        self.permit = permit
        self.judgment_model = judgment_model or config.DIALOGUE_ANALYSIS_MODEL
        self.db_session = db_session
        self.template_id = template_id
        self.model = model or config.ANALYSIS_MODEL
        self.reasoning_effort = reasoning_effort or config.TUTOR_REASONING
        self.max_tokens = max_tokens or config.TUTOR_MAX_TOKENS
        self.intervention_threshold = (
            intervention_threshold or config.TUTOR_INTERVENTION_THRESHOLD
        )
        self.scenario_title = scenario_title
        self.prompt = prompt
        self.student_profile = student_profile
        self.intervention_count = initial_intervention_count
        self.question_count = initial_question_count
        self.sensitivity_config = SENSITIVITY_PRESETS.get(
            sensitivity, SENSITIVITY_PRESETS["medium"]
        )

    async def invoke(self, operation, model_id, options, instruction, messages):
        if self.permit is not None:
            permit, self.permit = self.permit, None
            permit.task = asyncio.current_task()
        else:
            async with self.factory() as db:
                connection, model, options = await resolve_lesson_model(
                    db, model_id, "mentor", options
                )
            permit = await admit_call(
                self.factory,
                connection_id=connection.id,
                owner_id=self.owner_id,
                operation=operation,
                role="mentor",
                admin=False,
                model_config_id=model.id,
                expected_model_version=model.config_version,
            )
        values = dict(
            provider="openai",
            model_id=model_id,
            role="mentor",
            system_instruction=instruction,
            messages=messages,
            validated_options=options,
            request_id=self.request_id or str(uuid4()),
        )
        judgment = operation == "mentor_judgment"
        request = (
            StructuredRequest(
                **values, output_schema=LessonInterventionJudgment
            )
            if judgment
            else TextRequest(**values)
        )
        async with aclosing(
            execute_call(
                permit,
                request,
                kind="structured" if judgment else "text",
                run_id=self.run_id,
                session_id=self.session_id,
            )
        ) as events:
            async for event in events:
                if event.type == "completed":
                    return event
                if event.type in ("error", "refused", "interrupted"):
                    raise InvocationError(event.error_code or event.type)

    async def close(self):
        if self.permit is not None:
            self.permit.release()
            self.permit = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    def should_intervene(self, recent_teacher_questions: list[str]) -> bool:
        """Determine if tutor should provide feedback (legacy method)."""
        self.question_count, self.intervention_count = advance_question_count(
            self.question_count, self.intervention_count
        )

        if self.intervention_count >= self.intervention_threshold:
            return False

        if len(recent_teacher_questions) < 2:
            return False

        latest = recent_teacher_questions[-1]

        if check_low_leverage_patterns(latest):
            return True

        if check_vague_patterns(recent_teacher_questions):
            return True

        return False

    async def analyze_conversation_with_llm(
        self, pairs: list[tuple[str, str]]
    ) -> dict:
        """Analyze dialogue pairs using LLM for semantic similarity."""
        if len(pairs) < 2:
            return {
                "is_repetitive": False,
                "is_inappropriate": False,
                "reason": "",
            }

        # Format recent pairs for analysis
        context = ""
        for i, (teacher, student) in enumerate(pairs, 1):
            context += f"[대화 {i}]\n교사: {teacher}\n학생: {student}\n\n"

        analysis_prompt = f"""다음 교사-학생 대화쌍들을 분석해주세요:

{context}

다음 기준으로 분석하세요:
1. 반복 대화: 교사 질문이나 학생 응답이 70% 이상 유사한 내용으로 반복되는가?
2. 부적절한 대화: 교사가 학생 응답을 무시하거나, 대화가 진전 없이 맴도는가?

JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{"is_repetitive": true/false,
"is_inappropriate": true/false,
"reason": "판단 근거"}}"""

        try:
            response = await self.invoke(
                "mentor_judgment",
                self.judgment_model,
                {"max_output_tokens": 200},
                "",
                [{"role": "user", "content": analysis_prompt}],
            )
            return response.structured
        except InvocationError as error:
            if error.code in (
                "configuration_unavailable",
                "call_limit_reached",
                "interrupted",
                "timeout_total",
                "timeout_first_output",
            ):
                raise
            logger.warning("Mentor judgment fallback: %s", error.code)

        # Fallback to simple Jaccard similarity
        is_repetitive = detect_repetitive_dialogue_simple(pairs)
        return {
            "is_repetitive": is_repetitive,
            "is_inappropriate": False,
            "reason": "반복적인 대화 패턴 감지" if is_repetitive else "",
        }

    async def analyze_conversation(
        self,
        recent_exchanges: list[dict],
        current_teacher: str,
        current_student: str,
        *,
        question_counted: bool = False,
    ) -> tuple[bool, str | None]:
        """Analyze conversation pairs to determine intervention need."""
        decision, reason = self.intervention_decision(
            recent_exchanges,
            current_teacher,
            current_student,
            question_counted=question_counted,
        )
        if decision is not None:
            return decision, reason
        pairs = extract_recent_pairs(recent_exchanges, max_pairs=2)
        pairs.append((current_teacher, current_student))
        judgment = await self.analyze_conversation_with_llm(pairs)
        return self.intervention_decision(
            recent_exchanges,
            current_teacher,
            current_student,
            question_counted=True,
            judgment=judgment,
        )

    def intervention_decision(
        self,
        recent_exchanges,
        current_teacher,
        current_student,
        *,
        question_counted=False,
        judgment=None,
    ):
        """None means the unchanged policy needs a semantic judgment call."""
        sc = self.sensitivity_config

        # Rate limiting
        if not question_counted:
            self.question_count, self.intervention_count = (
                advance_question_count(
                    self.question_count, self.intervention_count
                )
            )

        if self.intervention_count >= self.intervention_threshold:
            return False, None

        # Extract recent pairs + current exchange
        pairs = extract_recent_pairs(recent_exchanges, max_pairs=2)
        pairs.append((current_teacher, current_student))

        # Collect recent teacher questions for pattern checks
        teacher_questions = [
            ex["content"]
            for ex in recent_exchanges[-5:]
            if ex["role"] == "teacher"
        ]

        # Need at least 2 pairs for comparison
        if len(pairs) < 2:
            if check_low_leverage_patterns(
                current_teacher,
                recent_questions=teacher_questions,
                min_count=sc["low_leverage_count"],
            ):
                return True, "low_leverage"
            return False, None

        # Fast Jaccard similarity check (uses sensitivity threshold)
        if detect_repetitive_dialogue_simple(
            pairs, threshold=sc["similarity_threshold"]
        ):
            return True, "반복적인 대화 패턴이 감지되었습니다."

        # LLM semantic analysis (skipped if sensitivity disables it)
        if sc["use_llm"]:
            if judgment is None:
                return None, None
            if judgment["is_repetitive"]:
                return True, (judgment["reason"] or "반복적인 대화 패턴 감지")
            if judgment["is_inappropriate"]:
                return True, (judgment["reason"] or "대화 진전 없음")

        # Check vague patterns with sensitivity min_matches
        all_teacher_questions = teacher_questions + [current_teacher]
        if check_vague_patterns(
            all_teacher_questions,
            min_matches=sc["vague_min_matches"],
        ):
            return True, "모호한 질문 패턴 감지"

        # Check low-leverage with sensitivity min_count
        if check_low_leverage_patterns(
            current_teacher,
            recent_questions=all_teacher_questions,
            min_count=sc["low_leverage_count"],
        ):
            return True, "low_leverage"

        return False, None

    async def generate_feedback(
        self,
        teacher_question: str,
        student_response: str,
        recent_exchanges: list[dict],
        *,
        template_text: str | None = None,
        question_counted: bool = False,
    ) -> tuple[str | None, Optional[dict]]:
        """Generate pedagogical feedback for teacher."""
        should_intervene, reason = await self.analyze_conversation(
            recent_exchanges,
            teacher_question,
            student_response,
            question_counted=question_counted,
        )

        if not should_intervene:
            return None, None

        try:
            template = template_text
            if template is None:
                template = await PromptManager.get_template_text_by_id(
                    self.db_session, self.template_id
                )

            system_prompt = template.format(
                scenario_title=self.scenario_title,
                prompt=self.prompt,
                student_profile=self.student_profile,
            )

            context = "Recent conversation:\n"
            for ex in recent_exchanges[-4:]:
                context += f"{ex['role'].upper()}: {ex['content']}\n"
            context += f"TEACHER: {teacher_question}\n"
            context += f"STUDENT: {student_response}\n"

            if reason and reason != "low_leverage":
                context += f"\n개입 이유: {reason}\n"

            input_messages = [
                {"role": "developer", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{context}\n"
                        "교사를 위한 간단하고 건설적인 "
                        "피드백을 한글로 제공해주세요."
                    ),
                },
            ]

            response = await self.invoke(
                "mentor",
                self.model,
                {
                    "max_output_tokens": self.max_tokens,
                    "reasoning": {"effort": self.reasoning_effort},
                },
                input_messages[0]["content"],
                input_messages[1:],
            )
            self.intervention_count += 1
            return response.text, None
        except (ValueError, KeyError):
            raise InvocationError("configuration_unavailable") from None

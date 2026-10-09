"""SessionManager service for orchestrating dialogue flow."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.session_helpers import mark_session_ended
from src.config import config
from src.models import Message, Scenario, Session
from src.services.lesson_snapshots import load_active_lesson
from src.services.student_bot import StudentBot
from src.services.turn_context import load_completed_turns
from src.services.tutor_bot import TutorBot

logger = logging.getLogger(__name__)


class SessionManager:
    """Orchestrates teacher-student-tutor dialogue interactions."""

    def __init__(self, db_session: AsyncSession, session_id: int):
        """Initialize SessionManager for specific session.

        Args:
            db_session: Database session
            session_id: Dialogue session ID
        """
        self.db = db_session
        self.session_id = session_id
        self.student_bot = None
        self.tutor_bot = None  # Initialized conditionally in initialize()

    async def close(self):
        """Close all owned clients, including partial initialization."""
        for service in (self.student_bot, self.tutor_bot):
            if service is not None:
                await service.close()

    async def initialize(self) -> None:
        """Validate the native lesson before initializing its bots."""
        # Load session and scenario
        result = await self.db.execute(
            select(Session).where(
                Session.id == self.session_id, Session.deleted_at.is_(None)
            )
        )
        session = result.scalar_one()
        await load_active_lesson(self.db, self.session_id, session.teacher_id)
        tutor_intervention_count = session.tutor_intervention_count
        tutor_question_count = session.tutor_question_count

        result = await self.db.execute(
            select(Scenario).where(Scenario.id == session.scenario_id)
        )
        scenario = result.scalar_one()

        # Load bot configuration from .env and scenario overrides
        bot_config = self._load_bot_config(scenario)

        # Initialize StudentBot with scenario context and configuration
        self.student_bot = StudentBot(
            session_id=self.session_id,
            owner_id=session.teacher_id,
            db_session=self.db,
        )

        # Conditionally initialize TutorBot based on scenario setting
        if bot_config["tutor_enabled"] and scenario.tutor_template_id:
            self.tutor_bot = TutorBot(
                session_id=self.session_id,
                owner_id=session.teacher_id,
                db_session=self.db,
                template_id=scenario.tutor_template_id,
                scenario_title=scenario.title,
                prompt=scenario.prompt,
                student_profile=scenario.student_profile or "Grade 5 student",
                model=bot_config["tutor_model"],
                reasoning_effort=bot_config["tutor_reasoning"],
                max_tokens=bot_config["tutor_max_tokens"],
                intervention_threshold=bot_config[
                    "tutor_intervention_threshold"
                ],
                initial_intervention_count=tutor_intervention_count,
                initial_question_count=tutor_question_count,
                sensitivity=bot_config["tutor_sensitivity"],
            )
        else:
            self.tutor_bot = None  # TutorBot disabled for this scenario

    async def process_teacher_message(
        self, teacher_content: str
    ) -> list[Message]:
        """Process teacher message and generate bot responses.

        Args:
            teacher_content: Teacher's question/message

        Returns:
            List of new Message objects (teacher, student, optional tutor)
        """
        if not self.student_bot:
            await self.initialize()

        new_messages = []

        # 1. Load conversation history (before saving teacher message)
        history = await self._get_conversation_history()

        # 2. Save teacher message
        teacher_msg = Message(
            session_id=self.session_id,
            role="teacher",
            content=teacher_content,
        )
        self.db.add(teacher_msg)
        await self.db.flush()  # Get ID without commit
        await self.db.commit()  # Teacher 메시지 즉시 가시화
        new_messages.append(teacher_msg)

        # 3. Generate student response (must be sequential - needed by others)
        (
            student_content,
            _student_usage,
        ) = await self.student_bot.generate_response(teacher_content)

        tutor_feedback = None
        if self.tutor_bot:
            try:
                tutor_feedback, _ = await self.tutor_bot.generate_feedback(
                    teacher_content, student_content, history
                )
            except Exception:
                logger.warning("TutorBot feedback failed")

        # Turn-level misconception calls are retired (ADR-0003).
        student_msg = Message(
            session_id=self.session_id,
            role="student",
            content=student_content,
        )
        self.db.add(student_msg)
        await self.db.flush()
        new_messages.append(student_msg)

        # 4. Save optional mentor feedback after its independently logged call.
        if tutor_feedback:
            tutor_msg = Message(
                session_id=self.session_id,
                role="tutor",
                content=tutor_feedback,
            )
            self.db.add(tutor_msg)
            await self.db.flush()
            new_messages.append(tutor_msg)

        # 5. Refresh to get created_at timestamps (dependency auto-commits)
        for msg in new_messages:
            await self.db.refresh(msg)

        # Persist TutorBot state to session
        if self.tutor_bot:
            result = await self.db.execute(
                select(Session).where(Session.id == self.session_id)
            )
            sess = result.scalar_one()
            sess.tutor_intervention_count = self.tutor_bot.intervention_count
            sess.tutor_question_count = self.tutor_bot.question_count
            await self.db.flush()

        return new_messages

    def _load_bot_config(self, scenario: Scenario) -> dict:
        """Load bot config from .env and scenario overrides.

        Configuration priority:
        1. Scenario-specific overrides (if set)
        2. Environment variables (.env)
        3. Hardcoded defaults

        Note: Temperature is not supported for GPT-5 Responses API.
              Use reasoning_effort (minimal/low/medium/high) instead.

        Args:
            scenario: Scenario model with optional bot config overrides

        Returns:
            Dictionary with complete bot configuration parameters:
            - tutor_model, tutor_reasoning, tutor_max_tokens
            - tutor_enabled, tutor_intervention_threshold
        """
        return {
            # TutorBot configuration
            "tutor_enabled": scenario.tutor_enabled,
            "tutor_model": config.ANALYSIS_MODEL,
            "tutor_reasoning": config.TUTOR_REASONING or "low",
            "tutor_max_tokens": config.TUTOR_MAX_TOKENS or 750,
            "tutor_intervention_threshold": (
                scenario.tutor_intervention_threshold
                if scenario.tutor_intervention_threshold is not None
                else config.TUTOR_INTERVENTION_THRESHOLD or 3
            ),
            "tutor_sensitivity": (
                scenario.tutor_sensitivity
                if hasattr(scenario, "tutor_sensitivity")
                else "medium"
            ),
        }

    async def _get_conversation_history(self) -> list[dict]:
        """Retrieve only the last N completed teacher–student pairs."""
        return await load_completed_turns(self.db, self.session_id)

    async def end_session(self) -> None:
        """Mark session as ended."""
        result = await self.db.execute(
            select(Session).where(
                Session.id == self.session_id, Session.deleted_at.is_(None)
            )
        )
        session = result.scalar_one()

        await mark_session_ended(session, self.db, force=True)


# TODO: Task 3.1.2/3.1.3 - API Usage Logging Tests
# TODO: test_api_usage_logging_student_message
#       - Verify StudentBot API call generates usage log entry
#       - Check prompt_tokens, completion_tokens, total_tokens accuracy
#       - Validate estimated_cost_usd calculation
# TODO: test_api_usage_logging_tutor_intervention
#       - Verify TutorBot API call generates usage log entry (when intervening)
#       - Check bot_type='tutor' correctly logged
#       - Validate model name matches tutor configuration
# TODO: test_api_usage_logging_failure_handling
#       - Verify logging failure doesn't break dialogue flow
#       - Check warning logs when usage info unavailable
#       - Ensure DB rollback doesn't affect message persistence

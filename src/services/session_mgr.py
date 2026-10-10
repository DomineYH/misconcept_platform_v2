"""SessionManager service for orchestrating dialogue flow."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.session_helpers import mark_session_ended
from src.models import Message, Session
from src.services.lesson_snapshots import load_active_lesson
from src.services.student_bot import StudentBot


class SessionManager:
    """Orchestrates teacher–student dialogue; mentor runs independently."""

    def __init__(self, db_session: AsyncSession, session_id: int):
        """Initialize SessionManager for specific session.

        Args:
            db_session: Database session
            session_id: Dialogue session ID
        """
        self.db = db_session
        self.session_id = session_id
        self.student_bot = None

    async def close(self):
        """Close all owned clients, including partial initialization."""
        if self.student_bot is not None:
            await self.student_bot.close()

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
        # Initialize StudentBot with scenario context and configuration
        self.student_bot = StudentBot(
            session_id=self.session_id,
            owner_id=session.teacher_id,
            db_session=self.db,
        )

    async def process_teacher_message(
        self, teacher_content: str
    ) -> list[Message]:
        """Process teacher message and generate bot responses.

        Args:
            teacher_content: Teacher's question/message

        Returns:
            List of new Message objects (teacher, student)
        """
        if not self.student_bot:
            await self.initialize()

        new_messages = []
        permit, request = await self.student_bot.prepare_response(
            teacher_content
        )

        # Save teacher message
        teacher_msg = Message(
            session_id=self.session_id,
            role="teacher",
            content=teacher_content,
        )
        self.db.add(teacher_msg)
        try:
            await self.db.flush()  # Get ID without commit
            await self.db.commit()  # Teacher 메시지 즉시 가시화
        except BaseException:
            permit.release()
            raise
        new_messages.append(teacher_msg)

        # Generate the student response before saving its completed text.
        (
            student_content,
            _student_usage,
        ) = await self.student_bot.invoke_response(permit, request)

        # Turn-level misconception calls are retired (ADR-0003).
        student_msg = Message(
            session_id=self.session_id,
            role="student",
            content=student_content,
        )
        self.db.add(student_msg)
        await self.db.flush()
        new_messages.append(student_msg)

        # Refresh to get created_at timestamps (dependency auto-commits)
        for msg in new_messages:
            await self.db.refresh(msg)

        return new_messages

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

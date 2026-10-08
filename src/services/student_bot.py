"""StudentBot service for role-playing student with misconception."""

from contextlib import aclosing
from typing import Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import config
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError, TextRequest
from src.services.lesson_connections import resolve_lesson_model
from src.services.prompt_manager import PromptManager

BASE_STUDENT_PROMPT = (
    "## 필수 행동 규칙 (최우선 적용)\n\n"
    "1. 항상 존댓말(높임말)을 사용하여 대답하세요.\n"
    "2. 사용자에게 되묻는 질문을 하지 마세요. "
    "사용자가 묻는 말에만 답하세요."
)


def build_student_input(template, prompt, title, profile, teacher, history):
    system_prompt = template.format(
        scenario_title=title, student_profile=profile, prompt=prompt
    )
    messages = [
        {"role": "developer", "content": BASE_STUDENT_PROMPT},
        {"role": "developer", "content": system_prompt},
    ]
    roles = {"teacher": "user", "student": "assistant"}
    messages.extend(
        {"role": roles[msg["role"]], "content": msg["content"]}
        for msg in history
        if msg["role"] in roles
    )
    messages.append({"role": "user", "content": teacher})
    return messages


class StudentBot:
    """Chatbot simulating student with specific misconception."""

    def __init__(
        self,
        scenario_prompt: str,
        scenario_title: str,
        student_profile: str,
        db_session: AsyncSession,
        template_id: int,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        *,
        session_id=None,
        owner_id=None,
    ):
        """Keep S0 prompt/options; credentials are resolved for each call."""
        self.session_id = session_id
        self.owner_id = owner_id
        self.db_session = db_session
        self.template_id = template_id
        self.model = model or config.CHAT_MODEL
        self.reasoning_effort = reasoning_effort or config.STUDENT_REASONING
        self.max_tokens = max_tokens or config.STUDENT_MAX_TOKENS

        # Store scenario context for dynamic prompt formatting
        self.scenario_prompt = scenario_prompt
        self.scenario_title = scenario_title
        self.student_profile = student_profile

    async def generate_response(
        self, teacher_message: str, conversation_history: list[dict]
    ) -> tuple[str, Optional[dict]]:
        """Execute one nonstream student invocation, with no automatic retry."""
        factory = async_sessionmaker(
            self.db_session.bind, expire_on_commit=False, autoflush=False
        )
        async with factory() as db:
            connection, model, options = await resolve_lesson_model(
                db,
                self.model,
                "student",
                {
                    "max_output_tokens": self.max_tokens,
                    "reasoning": {"effort": self.reasoning_effort},
                },
            )
            try:
                template = await PromptManager.get_template_text_by_id(
                    db, self.template_id
                )
                messages = build_student_input(
                    template,
                    self.scenario_prompt,
                    self.scenario_title,
                    self.student_profile,
                    teacher_message,
                    conversation_history,
                )
            except (ValueError, KeyError):
                raise InvocationError("configuration_unavailable") from None
        request = TextRequest(
            "openai",
            self.model,
            "student",
            "\n\n".join(m["content"] for m in messages[:2]),
            messages[2:],
            options,
            str(uuid4()),
        )
        permit = await admit_call(
            factory,
            connection_id=connection.id,
            owner_id=self.owner_id,
            operation="student",
            role="student",
            admin=False,
            expected_connection_version=connection.connection_version,
            model_config_id=model.id,
            expected_model_version=model.config_version,
        )
        async with aclosing(
            execute_call(
                permit,
                request,
                session_id=self.session_id,
            )
        ) as events:
            async for event in events:
                if event.type != "completed":
                    raise InvocationError(event.error_code)
                usage = event.usage
                return event.text, (
                    {
                        "prompt_tokens": usage["input_tokens"],
                        "completion_tokens": usage["output_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                    if usage and usage["raw_usage_json"] is not None
                    else None
                )

    async def close(self):
        """Each invocation owns and closes its SDK client."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

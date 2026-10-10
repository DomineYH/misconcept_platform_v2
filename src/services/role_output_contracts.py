"""Strict role outputs with server semantics and no repair/fallback behavior."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from src.api.schemas.analysis import DetailedReasoning
from src.services.invocation_types import InvocationError


class RoleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MentorOutput(RoleOutput):
    should_intervene: bool
    feedback: str = Field(max_length=50_000)
    reason_summary: str = Field(max_length=2_000)

    @model_validator(mode="after")
    def meaningful(self, info: ValidationInfo):
        context = info.context or {}
        if (
            (context.get("trigger") == "manual" and not self.should_intervene)
            or (
                "expected_should_intervene" in context
                and self.should_intervene
                != context["expected_should_intervene"]
            )
            or (not self.should_intervene and self.feedback != "")
        ):
            raise InvocationError("invalid_output")
        if not self.reason_summary.strip() or (
            self.should_intervene and not self.feedback.strip()
        ):
            raise InvocationError("empty_response")
        return self


class ClassificationReasoning(DetailedReasoning):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuestionClassification(RoleOutput):
    label: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reasoning: ClassificationReasoning

    @model_validator(mode="after")
    def meaningful(self, info: ValidationInfo):
        levels = (info.context or {}).get("labels", {})
        if self.label not in levels:
            raise InvocationError("invalid_reference")
        if not self.reasoning.summary.strip():
            raise InvocationError("empty_response")
        improved = self.reasoning.improved_sentence
        if (
            levels[self.label] == "low"
            and (not improved or not improved.strip())
        ) or (levels[self.label] != "low" and improved is not None):
            raise InvocationError("invalid_output")
        return self


class Strength(RoleOutput):
    message_id: int
    quote: str
    reason: str = Field(min_length=1)


class Improvement(RoleOutput):
    student_message_id: int
    student_quote: str
    missed_reason: str = Field(min_length=1)
    alternative_question: str = Field(min_length=1, max_length=60)
    alternative_reason: str = Field(min_length=1)


class DialogueCoaching(RoleOutput):
    message_id: int
    role: Literal["teacher", "student"]
    marker: Literal["good_moment", "missed_moment", "key_clue"]
    note: str = Field(min_length=1)


class SessionSynthesis(RoleOutput):
    brief_feedback: list[str]
    strengths: list[Strength]
    improvements: list[Improvement]
    dialogue_coaching: list[DialogueCoaching]

    @model_validator(mode="after")
    def meaningful(self, info: ValidationInfo):
        messages = {
            m["id"]: m for m in (info.context or {}).get("messages", [])
        }
        for item in self.strengths + self.improvements + self.dialogue_coaching:
            improvement = isinstance(item, Improvement)
            mid = item.student_message_id if improvement else item.message_id
            role = (
                "student"
                if improvement
                else ("teacher" if isinstance(item, Strength) else item.role)
            )
            message = messages.get(mid)
            if message is None or message["role"] != role:
                raise InvocationError("invalid_reference")
            if isinstance(item, (Strength, Improvement)):
                quote = item.student_quote if improvement else item.quote
                if not quote.strip() or quote not in message["content"]:
                    raise InvocationError("invalid_reference")
        if not self.brief_feedback or not (self.strengths or self.improvements):
            raise InvocationError("empty_response")
        for text in self.brief_feedback:
            if not text.strip():
                raise InvocationError("empty_response")
            if len(text.strip()) > 70 or text.strip().startswith(("{", "[")):
                raise InvocationError("invalid_output")
        return self


class RuntimeOutput(BaseModel):
    """Legacy analysis shapes; lesson services remain semantic authorities."""

    model_config = ConfigDict(extra="ignore", strict=True)

    @model_serializer(mode="wrap")
    def supplied_fields(self, handler):
        # Preserve absent legacy fields for the existing normalizers/validators.
        return {
            key: value
            for key, value in handler(self).items()
            if key in self.model_fields_set
        }


class RuntimeReasoning(RuntimeOutput):
    summary: str | None = None
    improved_sentence: str | None = None


class RuntimeAnalysisOutput(RuntimeOutput):
    @model_validator(mode="after")
    def legacy_semantics(self, info: ValidationInfo):
        context = info.context or {}
        if normalize := context.get("normalize"):
            # Execute legacy semantics before the common boundary finalizes usage.
            context["normalized"] = normalize(self.model_dump())
        return self


class RuntimeClassification(RuntimeAnalysisOutput):
    label: str
    confidence: float | str
    reasoning: RuntimeReasoning | str | None = None


class RuntimeGreeting(RuntimeOutput):
    index: int | None = None
    is_greeting: bool = False
    reason: str | None = None


class RuntimeGreetings(RuntimeOutput):
    results: list[RuntimeGreeting]


class RuntimeStrength(RuntimeOutput):
    message_id: int | None = None
    quote: str | None = None
    reason: str | None = None


class RuntimeImprovement(RuntimeOutput):
    student_message_id: int | None = None
    student_quote: str | None = None
    missed_reason: str | None = None
    alternative_question: str | None = None
    alternative_reason: str | None = None


class RuntimeCoaching(RuntimeOutput):
    message_id: int | None = None
    role: str | None = None
    marker: str | None = None
    note: str | None = None


class RuntimeSynthesis(RuntimeAnalysisOutput):
    brief_feedback: list[str | None] | None = None
    strengths: list[RuntimeStrength | None] | None = None
    improvements: list[RuntimeImprovement | None] | None = None
    dialogue_coaching: list[RuntimeCoaching | None] | None = None

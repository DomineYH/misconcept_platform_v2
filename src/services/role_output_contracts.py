"""Existing S1 output fields, validated without legacy repair/fallback behavior."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    model_validator,
)

from src.api.schemas.analysis import DetailedReasoning
from src.services.invocation_types import InvocationError


class RoleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InterventionJudgment(RoleOutput):
    is_repetitive: bool
    is_inappropriate: bool
    reason: str

    @model_validator(mode="after")
    def meaningful(self):
        if (
            self.is_repetitive or self.is_inappropriate
        ) and not self.reason.strip():
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
    quote: str = Field(min_length=1)
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

"""Strict role outputs with server semantics and no repair/fallback behavior."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    model_validator,
)

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

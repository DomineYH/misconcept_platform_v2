"""Pinned permissive S2 output contracts, isolated from product execution."""

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    model_serializer,
    model_validator,
)


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

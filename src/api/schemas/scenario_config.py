"""Bounded, literal v1 authoring values; completeness belongs to publication."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)


class ConfigValue(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        str_max_length=50000,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def utf8_text(cls, value):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("invalid_utf8") from None
        return value


class Effort(ConfigValue):
    effort: str = Field(min_length=1, max_length=50)


class AnthropicThinking(ConfigValue):
    type: Literal["disabled", "adaptive"]


class AnthropicThinkingBudget(ConfigValue):
    type: Literal["enabled"]
    budget_tokens: StrictInt = Field(ge=1)


class GoogleThinkingBudget(ConfigValue):
    budget: StrictInt = Field(ge=-1)


class GoogleThinkingLevel(ConfigValue):
    level: str = Field(min_length=1, max_length=50)


class ModelOptions(ConfigValue):
    max_output_tokens: StrictInt | None = Field(default=None, ge=1)
    temperature: float | None = None
    reasoning: Effort | None = None
    thinking: (
        AnthropicThinking
        | AnthropicThinkingBudget
        | GoogleThinkingBudget
        | GoogleThinkingLevel
        | None
    ) = None
    output_config: Effort | None = None

    @field_validator("*", mode="before")
    @classmethod
    def no_explicit_null(cls, value):
        if value is None:
            raise ValueError("invalid_options")
        return value


class ResolvedModelConfig(ConfigValue):
    model_config_id: StrictInt = Field(ge=1)
    provider_connection_id: StrictInt = Field(ge=1)
    provider: Literal["openai", "anthropic", "google"]
    model_id: str = Field(min_length=1)
    options: ModelOptions

    @model_validator(mode="after")
    def provider_option_structure(self):
        allowed = {
            "openai": {"max_output_tokens", "temperature", "reasoning"},
            "anthropic": {
                "max_output_tokens",
                "temperature",
                "thinking",
                "output_config",
            },
            "google": {"max_output_tokens", "temperature", "thinking"},
        }
        if self.options.model_fields_set - allowed[self.provider]:
            raise ValueError("invalid_options")
        thinking = self.options.thinking
        if thinking is not None:
            expected = (
                (AnthropicThinking, AnthropicThinkingBudget)
                if self.provider == "anthropic"
                else (GoogleThinkingBudget, GoogleThinkingLevel)
            )
            if not isinstance(thinking, expected):
                raise ValueError("invalid_options")
        return self

    @field_serializer("options")
    def saved_options(self, value):
        return value.model_dump(exclude_unset=True)


class ProblemConfig(ConfigValue):
    public_text: str = ""
    learning_objective: str = ""


class StudentConfig(ConfigValue):
    name: str = Field(default="", max_length=50)
    public_profile: str = ""
    internal_profile: str = ""
    misconception: str = ""
    behavior_instruction: str = ""
    resolved_model_config: ResolvedModelConfig | None = None


PolicyInt = Annotated[StrictInt, Field(ge=1, le=1000)]


class InterventionPolicy(ConfigValue):
    condition: str = ""
    start_turn: PolicyInt = 3
    min_interval_turns: PolicyInt = 2
    window_turns: PolicyInt = 10
    max_interventions: PolicyInt = 3

    @model_validator(mode="after")
    def bounded_interventions(self):
        if self.max_interventions > self.window_turns:
            raise ValueError("max_interventions_exceeds_window")
        return self


class MentorConfig(ConfigValue):
    mode: Literal["off", "manual", "auto"] = "manual"
    name: str = Field(default="멘토", max_length=50)
    welcome_message: str = ""
    behavior_instruction: str = ""
    intervention_policy: InterventionPolicy = Field(
        default_factory=InterventionPolicy
    )
    resolved_model_config: ResolvedModelConfig | None = None


class RubricItem(ConfigValue):
    id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=100)
    criteria: str = ""
    level: Literal["high", "low"] | None = None


class AnalysisConfig(ConfigValue):
    context: str = ""
    expected_understanding: str = ""
    instruction: str = ""
    rubric_name: str = ""
    rubric_description: str = ""
    category_name: str = ""
    classification_enabled: bool = True
    rubric: list[RubricItem] = Field(default_factory=list, max_length=20)
    resolved_model_config: ResolvedModelConfig | None = None


class RuntimeConfig(ConfigValue):
    context_turn_limit: StrictInt = Field(default=10, ge=1, le=100)


class ScenarioConfig(ConfigValue):
    problem: ProblemConfig
    student: StudentConfig
    mentor: MentorConfig
    analysis: AnalysisConfig
    runtime: RuntimeConfig


class DraftCreate(ConfigValue):
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=100)
    target_grade: str = Field(default="", max_length=100)
    is_active: bool = True
    groups: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=list
    )
    config_schema_version: StrictInt = Field(ge=1, le=1)
    config: ScenarioConfig
    action: Literal["save_draft", "publish"]
    acknowledge_review: bool = False

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("title_required")
        return value.strip()


class DraftUpdate(DraftCreate):
    expected_version: StrictInt = Field(ge=1)


class RevisionInput(ConfigValue):
    expected_version: StrictInt = Field(ge=1)


class DraftSaved(ConfigValue):
    id: int
    version: int
    status: Literal["draft", "published"]
    review_required: bool
    review_reasons: list[dict]

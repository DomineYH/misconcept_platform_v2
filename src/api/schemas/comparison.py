"""Bounded editor inputs for a student comparison."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from src.api.schemas.scenario_config import (
    ConfigValue,
    ResolvedModelConfig,
    ScenarioConfig,
)


class ComparisonSource(ConfigValue):
    title: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=100)
    target_grade: str = Field(default="", max_length=100)
    is_active: bool = True
    groups: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=list
    )
    config_schema_version: StrictInt = Field(ge=1, le=1)
    config: ScenarioConfig
    action: Literal["compare"] = "compare"
    acknowledge_review: bool = False
    expected_version: StrictInt | None = Field(default=None, ge=1)
    scenario_id: StrictInt | None = Field(default=None, ge=1)
    source_version: StrictInt | None = Field(default=None, ge=1)
    source_kind: Literal["unsaved_draft"]

    @model_validator(mode="after")
    def source_identity(self):
        if (self.scenario_id is None) != (self.source_version is None):
            raise ValueError("invalid_source")
        return self


class ComparisonMessage(ConfigValue):
    role: Literal["teacher", "student"]
    content: str = Field(min_length=1, max_length=50000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("required")
        return value


class ComparisonPanel(ConfigValue):
    panel_id: str = Field(
        min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$"
    )
    selection: ResolvedModelConfig = Field(alias="model_config")


class ComparisonStopGeneration(ConfigValue):
    generation_id: UUID = Field(strict=False)


class ComparisonStopTarget(ComparisonStopGeneration):
    panel_id: str = Field(
        min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$"
    )


class ComparisonStop(ConfigValue):
    generations: list[ComparisonStopTarget] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def distinct_panels(self):
        if len({target.panel_id for target in self.generations}) != len(
            self.generations
        ):
            raise ValueError("duplicate_panel")
        return self


class ComparisonRequest(ConfigValue):
    request_id: str
    target_role: Literal["student"]
    source_snapshot: ComparisonSource
    canonical_context: list[ComparisonMessage] = Field(max_length=199)
    current_message: str = Field(min_length=1, max_length=50000)
    panels: list[ComparisonPanel] = Field(min_length=1, max_length=3)
    parent_run_id: str | None = None

    @field_validator("request_id", "parent_run_id")
    @classmethod
    def uuid_string(cls, value):
        return str(UUID(value)) if value is not None else None

    @field_validator("current_message")
    @classmethod
    def nonblank(cls, value):
        return ComparisonMessage.nonblank(value)

    @model_validator(mode="after")
    def complete_pairs(self):
        if len({panel.panel_id for panel in self.panels}) != len(self.panels):
            raise ValueError("duplicate_panel")
        if len(self.canonical_context) % 2 or any(
            item.role != ("teacher" if index % 2 == 0 else "student")
            for index, item in enumerate(self.canonical_context)
        ):
            raise ValueError("invalid_history")
        return self

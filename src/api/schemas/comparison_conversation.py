"""Actions on server-owned saved comparison inputs."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from src.api.schemas.comparison import ComparisonStopTarget
from src.api.schemas.scenario_config import ConfigValue, ModelOptions


class ConversationPanel(ConfigValue):
    panel_id: str = Field(
        min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$"
    )
    model_config_id: StrictInt = Field(ge=1)
    role_options: dict[
        Literal["student", "mentor", "analysis"], ModelOptions
    ] = Field(default_factory=dict)


class ConversationCreate(ConfigValue):
    request_id: UUID = Field(strict=False)
    scenario_id: StrictInt = Field(ge=1)
    expected_scenario_version: StrictInt = Field(ge=1)
    panels: list[ConversationPanel] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_panels(self):
        if len({panel.panel_id for panel in self.panels}) != len(self.panels):
            raise ValueError("duplicate_panel")
        return self


class ConversationAction(ConfigValue):
    request_id: UUID = Field(strict=False)
    expected_version: StrictInt = Field(ge=1)


class ConversationTurn(ConversationAction):
    message: str = Field(min_length=1, max_length=50000)


class ConversationAdopt(ConversationAction):
    child_id: UUID = Field(strict=False)
    panel_id: str = Field(
        min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$"
    )
    generation_id: UUID = Field(strict=False)


class ConversationMentor(ConversationAction):
    target_turn_id: UUID = Field(strict=False)
    trigger: Literal["manual", "auto"]


class ConversationRetry(ConversationAction):
    generation_id: UUID = Field(strict=False)


class ConversationStop(ConversationAction):
    child_id: UUID = Field(strict=False)
    generations: list[ComparisonStopTarget] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_panels(self):
        if len({target.panel_id for target in self.generations}) != len(
            self.generations
        ):
            raise ValueError("duplicate_panel")
        return self

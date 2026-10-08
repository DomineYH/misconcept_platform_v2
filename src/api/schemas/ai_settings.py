"""Strict singleton authoring defaults and call limit/timeout values."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class SettingsValue(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RoleDefaults(SettingsValue):
    student: StrictInt | None = Field(ge=1)
    mentor: StrictInt | None = Field(ge=1)
    analysis: StrictInt | None = Field(ge=1)


class CallLimits(SettingsValue):
    total: StrictInt = Field(ge=2)
    openai: StrictInt = Field(ge=2)
    anthropic: StrictInt = Field(ge=2)
    google: StrictInt = Field(ge=2)
    admin: StrictInt = Field(ge=1, le=3)

    @model_validator(mode="after")
    def preserve_classroom_capacity(self):
        if self.admin >= self.total:
            raise ValueError("admin_limit_must_be_lower_than_total")
        return self


class CallTimeouts(SettingsValue):
    connect: StrictInt = Field(gt=0)
    student_first_output: StrictInt = Field(gt=0)
    student_total: StrictInt = Field(gt=0)
    mentor_first_output: StrictInt = Field(gt=0)
    mentor_total: StrictInt = Field(gt=0)
    analysis_total: StrictInt = Field(gt=0)
    model_list_total: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def first_output_before_total(self):
        if (
            self.student_first_output > self.student_total
            or self.mentor_first_output > self.mentor_total
        ):
            raise ValueError("first_output_exceeds_total")
        return self


class SettingsUpdate(SettingsValue):
    expected_version: StrictInt = Field(gt=0)
    defaults: RoleDefaults
    limits: CallLimits
    timeouts: CallTimeouts

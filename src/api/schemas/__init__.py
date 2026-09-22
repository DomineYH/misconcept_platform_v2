"""API Pydantic schemas."""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from src.api.schemas.contributor import (
    ContributorCreate,
    ContributorResponse,
    ContributorUpdate,
)
from src.api.schemas.group import (
    AdminGroupResponse,
    GroupCreate,
    GroupUpdate,
)
from src.api.schemas.user import (
    AdminUserResponse,
    BulkFailure,
    BulkPreviewResponse,
    BulkPreviewRow,
    BulkRegisterRequest,
    BulkRegisterResponse,
    BulkUserEntry,
    UserCreate,
    UserUpdate,
)


# Framework Schemas
class LabelItem(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    criteria: str = Field("", max_length=500)
    level: Literal["high", "low"] | None = None


class FrameworkCreateWeb(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(..., max_length=500)
    category_name: str | None = Field(None, max_length=100)
    labels: list[LabelItem] = Field(..., min_length=2, max_length=20)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, v):
        if len(v) < 2:
            raise ValueError("At least 2 labels required")
        if len(v) > 20:
            raise ValueError("Maximum 20 labels allowed")
        for item in v:
            if len(item.name) < 2:
                raise ValueError(f"Label '{item.name}' too short")
            if len(item.name) > 50:
                raise ValueError("Label too long (max 50)")
        return v


class FrameworkUpdateWeb(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = Field(None, max_length=500)
    category_name: str | None = Field(None, max_length=100)
    labels: list[LabelItem] | None = None


class AdminFrameworkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    category_name: str | None = None
    labels_json: str
    created_at: datetime | None = None


# Scenario Schemas
class ScenarioCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    prompt: str = Field(..., min_length=10, max_length=10000)
    student_profile: str = Field(..., min_length=3, max_length=5000)
    student_name: str | None = Field(None, max_length=50)
    subject: str | None = Field(None, max_length=100)
    problem_situation: str | None = Field(None, max_length=5000)
    greeting_message: str | None = Field(None, max_length=5000)
    framework_id: int
    is_active: bool = True

    # Video fields
    video_url: str | None = None
    video_transcript: str | None = None

    # Bot overrides (Phase 2)
    chat_model: str | None = None
    chat_temperature: float | None = 0.7
    tutor_intervention_threshold: int | None = 3

    # Tutor sensitivity
    tutor_sensitivity: Literal["high", "medium", "low"] = "medium"

    # Template selection (Phase 2.5)
    student_template_id: int  # Required
    tutor_template_id: int | None = None  # tutor disabled

    # Group assignment
    group_ids: list[int] | None = None

    @field_validator("video_url")
    @classmethod
    def validate_video_url(cls, v):
        """Validate video URL format."""
        if v is None or v.strip() == "":
            return v
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("video_url must start with " "http:// or https://")
        return v

    @field_validator("chat_model")
    @classmethod
    def validate_chat_model(cls, v):
        """Validate chat model name."""
        if v is None:
            return v
        # gpt-4 family: any string starting with "gpt-4"
        if v.startswith("gpt-4"):
            return v
        # gpt-5 family: any string starting with "gpt-5"
        if v.startswith("gpt-5"):
            return v
        raise ValueError(
            f"Invalid model: {v}. " "Must start with gpt-4 or gpt-5"
        )

    @field_validator("chat_temperature")
    @classmethod
    def validate_chat_temperature(cls, v):
        """Validate temperature range."""
        if v is None:
            return v
        if not (0.0 <= v <= 2.0):
            raise ValueError("chat_temperature must be " "between 0.0 and 2.0")
        return v

    @field_validator("tutor_intervention_threshold")
    @classmethod
    def validate_tutor_threshold(cls, v):
        """Validate threshold range."""
        if v is None:
            return v
        if not (1 <= v <= 10):
            raise ValueError("tutor_intervention_threshold " "must be 1-10")
        return v


class ScenarioUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=200)
    prompt: str | None = Field(None, min_length=10, max_length=10000)
    student_profile: str | None = Field(None, min_length=3, max_length=5000)
    student_name: str | None = Field(None, max_length=50)
    subject: str | None = Field(None, max_length=100)
    problem_situation: str | None = Field(None, max_length=5000)
    greeting_message: str | None = Field(None, max_length=5000)
    framework_id: int | None = None
    is_active: int | None = Field(None, ge=0, le=1)

    # Video fields
    video_url: str | None = None
    video_transcript: str | None = None

    # Bot overrides
    chat_model: str | None = None
    chat_temperature: float | None = None
    tutor_intervention_threshold: int | None = None

    # Tutor sensitivity
    tutor_sensitivity: Literal["high", "medium", "low"] | None = None

    # Template selection
    student_template_id: int | None = None
    tutor_template_id: int | None = None

    # Group assignment
    group_ids: list[int] | None = None

    @field_validator("video_url")
    @classmethod
    def validate_video_url(cls, v):
        """Validate video URL format."""
        if v is None or v.strip() == "":
            return v
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("video_url must start with " "http:// or https://")
        return v


class AdminScenarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    prompt: str
    student_profile: str
    student_name: str | None = None
    subject: str | None = None
    problem_situation: str | None = None
    greeting_message: str | None = None
    framework_id: int
    is_active: int

    # Video fields
    video_url: str | None = None
    video_transcript: str | None = None

    # Bot overrides
    chat_model: str | None = None
    chat_temperature: float | None = None
    tutor_intervention_threshold: int | None = None
    tutor_sensitivity: str = "medium"

    # Template selection
    student_template_id: int
    tutor_template_id: int | None = None

    created_at: object | None = None
    updated_at: object | None = None

    @property
    def tutor_enabled(self) -> bool:
        """Tutor enabled if template assigned."""
        return self.tutor_template_id is not None


__all__ = [
    "LabelItem",
    "FrameworkCreateWeb",
    "FrameworkUpdateWeb",
    "AdminFrameworkResponse",
    "ScenarioCreate",
    "ScenarioUpdate",
    "AdminScenarioResponse",
    "UserCreate",
    "UserUpdate",
    "AdminUserResponse",
    "BulkPreviewRow",
    "BulkPreviewResponse",
    "BulkUserEntry",
    "BulkRegisterRequest",
    "BulkFailure",
    "BulkRegisterResponse",
    "GroupCreate",
    "GroupUpdate",
    "AdminGroupResponse",
    "ContributorCreate",
    "ContributorUpdate",
    "ContributorResponse",
]

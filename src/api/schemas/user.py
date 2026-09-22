"""User Pydantic schemas for admin API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

VALID_USER_ROLES = ("teacher", "admin")


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    nickname: str = Field(..., min_length=2, max_length=30)
    role: str = Field(default="teacher")
    group_id: int | None = None


class UserUpdate(BaseModel):
    nickname: str | None = Field(None, min_length=2, max_length=30)
    role: str | None = None
    group_id: int | None = None
    password: str | None = Field(None, min_length=8, max_length=128)


class BulkPatternCreate(BaseModel):
    start_username: str = Field(..., min_length=3, max_length=50)
    count: int = Field(..., ge=1)
    role: Literal["teacher", "admin"] = "teacher"
    group_id: int | None = None


class BulkCsvRow(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    nickname: str = Field(..., min_length=2, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    role: Literal["teacher", "admin"]
    group_name: str | None = Field(default=None, max_length=100)

    @field_validator("username", "nickname", "password", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group_name(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class BulkCreateError(BaseModel):
    row: int | None = None
    username: str | None = None
    message: str


class BulkCreateSummary(BaseModel):
    created_count: int
    failed_count: int
    created_usernames: list[str] = Field(default_factory=list)
    errors: list[BulkCreateError] = Field(default_factory=list)


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str
    role: str
    group_id: int | None = None
    group_name: str | None = None
    created_at: datetime | None = None


class BulkPreviewRow(BaseModel):
    """Single row in bulk upload preview."""

    row_num: int
    username: str
    nickname: str
    role: str = "teacher"
    group_name: str | None = None
    group_id: int | None = None
    errors: list[str] = []


class BulkPreviewResponse(BaseModel):
    """Response from CSV preview endpoint."""

    rows: list[BulkPreviewRow]
    groups: list[dict]
    summary: dict


class BulkUserEntry(BaseModel):
    """Single user entry for bulk registration."""

    username: str
    nickname: str
    role: str = "teacher"
    group_id: int | None = None


class BulkRegisterRequest(BaseModel):
    """Request body for bulk user registration."""

    users: list[BulkUserEntry] = Field(
        ..., max_length=100
    )


class BulkFailure(BaseModel):
    """Single failure entry in bulk registration result."""

    username: str
    nickname: str
    reason: str


class BulkRegisterResponse(BaseModel):
    """Response from bulk registration endpoint."""

    success_count: int
    fail_count: int
    failures: list[BulkFailure]

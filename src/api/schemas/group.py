"""Group Pydantic schemas for admin API."""
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(
        None, max_length=500
    )


class GroupUpdate(BaseModel):
    name: str | None = Field(
        None, min_length=2, max_length=100
    )
    description: str | None = Field(
        None, max_length=500
    )


class AdminGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    created_at: datetime | None = None
    member_count: int = 0
    scenario_count: int = 0

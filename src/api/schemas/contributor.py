"""Pydantic schemas for Contributor (About page)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContributorCreate(BaseModel):
    """Schema for creating a new contributor."""

    name: str = Field(..., min_length=1, max_length=100)
    affiliation: str = Field(..., min_length=1, max_length=200)
    bio: str = Field(..., min_length=1, max_length=2000)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=200)
    sort_order: int = Field(0, ge=0)


class ContributorUpdate(BaseModel):
    """Schema for updating a contributor."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    affiliation: Optional[str] = Field(
        None, min_length=1, max_length=200
    )
    bio: Optional[str] = Field(None, min_length=1, max_length=2000)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = Field(None, ge=0)


class ContributorResponse(BaseModel):
    """Schema for contributor response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    affiliation: str
    bio: str
    phone: Optional[str] = None
    email: Optional[str] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

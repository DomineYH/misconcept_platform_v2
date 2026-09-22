"""Prompt schemas."""
from pydantic import BaseModel, Field

class PromptUpdateRequest(BaseModel):
    """Schema for updating a prompt template."""
    template_name: str = Field(..., min_length=3, max_length=100)
    template_text: str = Field(..., min_length=10, max_length=10000)


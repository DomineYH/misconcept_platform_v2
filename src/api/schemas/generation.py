"""Request identities shared by generation and analysis routes."""

from uuid import UUID

from pydantic import BaseModel, field_validator


class GenerationRequest(BaseModel):
    request_id: str

    @field_validator("request_id")
    @classmethod
    def uuid_string(cls, value):
        if value is not None:
            return str(UUID(value))
        return value


class AnalysisRequest(GenerationRequest):
    plan_hash: str | None = None

"""Public metadata, without storage identities or future extracted content."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ResumeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    original_filename: str
    media_type: str | None
    size_bytes: int | None = Field(gt=0)
    status: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ResumeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ResumeResponse]
    limit: int
    offset: int


class ResumeErrorResponse(BaseModel):
    detail: str

"""Strict, bounded career facts and an application-owned persistence envelope."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = 1
MAX_STRUCTURED_BYTES = 64 * 1024


def valid_text(value: str) -> str:
    if (
        not value.strip()
        or "\x00" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError("Invalid structured text")
    return value


ShortText = Annotated[
    str, StringConstraints(min_length=1, max_length=200), AfterValidator(valid_text)
]
DateText = Annotated[
    str, StringConstraints(min_length=1, max_length=100), AfterValidator(valid_text)
]
SkillText = Annotated[
    str, StringConstraints(min_length=1, max_length=100), AfterValidator(valid_text)
]
LongText = Annotated[
    str, StringConstraints(min_length=1, max_length=2000), AfterValidator(valid_text)
]
Highlight = Annotated[
    str, StringConstraints(min_length=1, max_length=1000), AfterValidator(valid_text)
]
Highlights = Annotated[list[Highlight], Field(max_length=10)]


class StrictResumeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always"
    )


class ResumeExperience(StrictResumeModel):
    job_title: ShortText | None
    company: ShortText | None
    location: ShortText | None
    start_date_text: DateText | None
    end_date_text: DateText | None
    highlights: Highlights


class ResumeEducation(StrictResumeModel):
    institution: ShortText | None
    degree: ShortText | None
    field_of_study: ShortText | None
    date_text: DateText | None
    highlights: Highlights


class ResumeProject(StrictResumeModel):
    name: ShortText | None
    description: LongText | None
    technologies: list[SkillText] = Field(max_length=30)
    highlights: Highlights


class ResumeCertification(StrictResumeModel):
    name: ShortText | None
    issuer: ShortText | None
    date_text: DateText | None


class StructuredResumeV1(StrictResumeModel):
    """Only explicitly supported career facts; missing facts are null or []."""

    summary: LongText | None
    skills: list[SkillText] = Field(max_length=100)
    experience: list[ResumeExperience] = Field(max_length=20)
    education: list[ResumeEducation] = Field(max_length=10)
    projects: list[ResumeProject] = Field(max_length=20)
    certifications: list[ResumeCertification] = Field(max_length=20)

    @model_validator(mode="after")
    def bounded_json(self):
        if len(self.model_dump_json().encode("utf-8")) > MAX_STRUCTURED_BYTES:
            raise ValueError("Structured result exceeds size policy")
        return self


class PersistedResumeInterpretation(StrictResumeModel):
    schema_version: Literal[1]
    model_identifier: ShortText
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: StructuredResumeV1

    @model_validator(mode="after")
    def bounded_json(self):
        if len(self.model_dump_json().encode("utf-8")) > MAX_STRUCTURED_BYTES:
            raise ValueError("Structured result exceeds size policy")
        return self


class ResumeInterpretationResponse(StrictResumeModel):
    resume_id: UUID
    schema_version: Literal[1]
    model_identifier: ShortText
    content: StructuredResumeV1

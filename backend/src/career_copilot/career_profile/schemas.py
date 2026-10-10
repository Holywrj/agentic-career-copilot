"""Small V1 input rules; stored responses are validated without normalization."""

import unicodedata
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)


def normalize_text(value: object, *, multiline: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("Must be a string")
    if multiline:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cs"} and not (multiline and character == "\n"):
            raise ValueError("Control and surrogate characters are not allowed")
        if not multiline and category in {"Zl", "Zp"}:
            raise ValueError("Must be a single line")
    return unicodedata.normalize("NFC", value).strip()


def normalize_summary(value: object) -> str | None:
    if value is None:
        return None
    return normalize_text(value, multiline=True) or None


def normalize_name(value: object) -> str:
    return normalize_text(value, multiline=False)


def deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


Summary = Annotated[str, Field(strict=True, max_length=2000)] | None
Skill = Annotated[str, Field(strict=True, min_length=1, max_length=80)]
Role = Annotated[str, Field(strict=True, min_length=1, max_length=120)]
Skills = Annotated[list[Skill], Field(max_length=100)]
Roles = Annotated[list[Role], Field(max_length=10)]
SummaryInput = Annotated[Summary, BeforeValidator(normalize_summary)]
SkillsInput = Annotated[
    list[Annotated[Skill, BeforeValidator(normalize_name)]], Field(max_length=100)
]
RolesInput = Annotated[
    list[Annotated[Role, BeforeValidator(normalize_name)]], Field(max_length=10)
]
Revision = Annotated[int, Field(strict=True, gt=0)]


class CareerProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    career_summary: SummaryInput = None
    skills: SkillsInput = Field(default_factory=list)
    target_roles: RolesInput = Field(default_factory=list)

    @field_validator("skills", "target_roles")
    @classmethod
    def unique_names(cls, values: list[str]) -> list[str]:
        # Field's count limit applies to the raw list before this deduplication.
        return deduplicate(values)


class CareerProfileUpdate(CareerProfileCreate):
    expected_revision: Revision
    career_summary: SummaryInput = Field(...)
    skills: SkillsInput = Field(...)
    target_roles: RolesInput = Field(...)


class CareerProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: Revision
    career_summary: Summary
    skills: Skills
    target_roles: Roles
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("career_summary")
    @classmethod
    def confirmed_summary(cls, value: str | None) -> str | None:
        if normalize_summary(value) != value:
            raise ValueError("Stored summary does not satisfy the V1 contract")
        return value

    @field_validator("skills", "target_roles")
    @classmethod
    def confirmed_names(cls, values: list[str]) -> list[str]:
        if any(normalize_name(value) != value for value in values):
            raise ValueError("Stored names do not satisfy the V1 contract")
        if deduplicate(values) != values:
            raise ValueError("Stored names must be unique")
        return values

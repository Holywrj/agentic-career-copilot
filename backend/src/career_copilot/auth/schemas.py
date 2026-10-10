"""Secret-bearing inputs are never used as public response models."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from career_copilot.auth.passwords import normalize_password, normalize_username


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(strict=True, max_length=64)]
    password: SecretStr

    @field_validator("username")
    @classmethod
    def canonical_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("password", mode="before")
    @classmethod
    def canonical_password(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Password must be a string")
        return normalize_password(value)


class RegisterRequest(LoginRequest):
    @field_validator("password")
    @classmethod
    def registration_password_policy(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if len(password) < 15 or password.isspace():
            raise ValueError("Password must contain 15-128 characters, not all spaces")
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: UserStatus


class RegisterResponse(UserResponse):
    username: str


class TokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int

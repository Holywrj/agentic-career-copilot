"""Explicit database configuration without import-time environment access."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


@dataclass(frozen=True)
class Settings:
    database_url: URL = field(repr=False)

    @classmethod
    def from_url(cls, value: str) -> "Settings":
        try:
            url = make_url(value)
        except (ArgumentError, ValueError):
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URL") from None
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError(
                "DATABASE_URL must use postgresql+psycopg and name a database"
            )
        return cls(database_url=url)

    @classmethod
    def from_env(cls) -> "Settings":
        value = os.environ.get("DATABASE_URL")
        if not value:
            raise ValueError("DATABASE_URL is required")
        return cls.from_url(value)


@dataclass(frozen=True)
class AuthSettings:
    """Application-only secrets; database migrations do not need a JWT key."""

    jwt_secret: str = field(repr=False)
    session_ttl_seconds: int = 30 * 60

    def __post_init__(self) -> None:
        if (
            not isinstance(self.jwt_secret, str)
            or not self.jwt_secret.isascii()
            or not self.jwt_secret.isprintable()
            or self.jwt_secret.isspace()
            or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must contain at least 32 random ASCII bytes")
        if (
            type(self.session_ttl_seconds) is not int
            or not 1 <= self.session_ttl_seconds <= 3600
        ):
            raise ValueError("JWT_SESSION_TTL_SECONDS must be between 1 and 3600")

    @classmethod
    def from_env(cls) -> "AuthSettings":
        secret = os.environ.get("JWT_SECRET")
        if not secret:
            raise ValueError("JWT_SECRET is required")
        try:
            ttl = int(os.environ.get("JWT_SESSION_TTL_SECONDS", "1800"))
        except ValueError:
            raise ValueError("JWT_SESSION_TTL_SECONDS must be an integer") from None
        return cls(jwt_secret=secret, session_ttl_seconds=ttl)


@dataclass(frozen=True)
class ResumeSettings:
    """Application-only storage configuration; migrations need only the database."""

    storage_dir: Path = field(repr=False)

    @classmethod
    def from_env(cls) -> "ResumeSettings":
        value = os.environ.get("RESUME_STORAGE_DIR")
        if not value or not value.strip():
            raise ValueError("RESUME_STORAGE_DIR is required")
        return cls(storage_dir=Path(value))


@dataclass(frozen=True)
class ResumeInterpretationSettings:
    """Read lazily by the interpreter; never required for application startup."""

    api_key: str | None = field(default=None, repr=False)
    base_url: str = field(default="https://api.deepseek.com", repr=False)
    model_identifier: str = "deepseek-flash"

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid DeepSeek configuration")
        if (
            not self.model_identifier.strip()
            or len(self.model_identifier) > 200
            or not self.model_identifier.isprintable()
        ):
            raise ValueError("Invalid DeepSeek configuration")

    @classmethod
    def from_env(cls) -> "ResumeInterpretationSettings":
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip() or None,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model_identifier=os.environ.get(
                "RESUME_INTERPRETATION_MODEL", "deepseek-flash"
            ),
        )

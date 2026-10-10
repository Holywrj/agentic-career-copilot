"""Explicit database configuration without import-time environment access."""

import os
from dataclasses import dataclass, field

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

"""Library-owned Argon2id, offloaded with a small per-worker concurrency bound."""

import re
import secrets
import unicodedata

from anyio import CapacityLimiter, to_thread
from pwdlib import PasswordHash

MAX_PASSWORD_BYTES = 512


def normalize_username(value: str) -> str:
    if len(value) > 64:
        raise ValueError("Username input is too large")
    stripped = value.strip()
    if not stripped.isascii():
        raise ValueError("Username must be ASCII")
    normalized = stripped.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", normalized):
        raise ValueError("Username must contain 3-32 allowed characters")
    return normalized


def normalize_password(value: str) -> str:
    # Bound the original input as well as the canonical form, before hashing.
    if len(value) > 128 or any(
        unicodedata.category(char) in {"Cc", "Cs"} for char in value
    ):
        raise ValueError("Invalid password size or control characters")
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password input is too large")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > 128 or len(normalized.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password input is too large")
    return normalized


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHash.recommended()
        self._limiter = CapacityLimiter(2)
        self._dummy_hash = ""

    @classmethod
    async def create(cls) -> "PasswordService":
        service = cls()
        service._dummy_hash = await service.hash(secrets.token_urlsafe(32))
        return service

    async def hash(self, password: str) -> str:
        return await to_thread.run_sync(
            self._hasher.hash, password, limiter=self._limiter
        )

    async def verify(self, password: str, password_hash: str | None) -> bool:
        matches = await to_thread.run_sync(
            self._hasher.verify,
            password,
            password_hash if password_hash is not None else self._dummy_hash,
            limiter=self._limiter,
        )
        return password_hash is not None and matches

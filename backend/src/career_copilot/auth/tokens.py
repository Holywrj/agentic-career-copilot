"""Fixed-policy JWT validation and one-way storage of the exact issued token."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt

from career_copilot.settings import AuthSettings

JWT_ISSUER = "career-copilot"
JWT_AUDIENCE = "career-copilot-api"
MAX_TOKEN_BYTES = 4096
REQUIRED_CLAIMS = ["sub", "jti", "iat", "exp", "iss", "aud"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class TokenClaims:
    user_id: UUID
    session_id: UUID
    issued_at: int
    expires_at: int


class InvalidAccessToken(Exception):
    """Safe boundary: never expose the token or the decoder's exception."""


class TokenService:
    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings

    def issue(self, claims: TokenClaims) -> str:
        return jwt.encode(
            {
                "sub": str(claims.user_id),
                "jti": str(claims.session_id),
                "iat": claims.issued_at,
                "exp": claims.expires_at,
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
            },
            self._settings.jwt_secret,
            algorithm="HS256",
        )

    def validate(self, token: str) -> TokenClaims:
        try:
            if not token.isascii() or len(token) > MAX_TOKEN_BYTES:
                raise ValueError
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=["HS256"],
                issuer=JWT_ISSUER,
                audience=JWT_AUDIENCE,
                options={
                    "require": REQUIRED_CLAIMS,
                    "strict_aud": True,
                    # Explicitly allow only a small future iat skew, not exp skew.
                    "verify_iat": False,
                },
            )
            issued_at, expires_at = payload["iat"], payload["exp"]
            if (
                type(issued_at) is not int
                or type(expires_at) is not int
                or issued_at < 0
                or issued_at >= expires_at
                or issued_at > int(utc_now().timestamp()) + 30
                or ("nbf" in payload and type(payload["nbf"]) is not int)
                or not isinstance(payload["sub"], str)
                or not isinstance(payload["jti"], str)
            ):
                raise ValueError
            # Reject NumericDates outside datetime's representable range before DB I/O.
            datetime.fromtimestamp(issued_at, UTC)
            datetime.fromtimestamp(expires_at, UTC)
            return TokenClaims(
                user_id=UUID(payload["sub"]),
                session_id=UUID(payload["jti"]),
                issued_at=issued_at,
                expires_at=expires_at,
            )
        except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
            raise InvalidAccessToken from None

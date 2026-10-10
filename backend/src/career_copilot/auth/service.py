"""Explicit transactions for local accounts and authoritative session lifecycle."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from career_copilot.auth.passwords import PasswordService
from career_copilot.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserStatus,
)
from career_copilot.auth.tokens import (
    InvalidAccessToken,
    TokenClaims,
    TokenService,
    hash_token,
    utc_now,
)
from career_copilot.db.models import AuthSession, LocalCredential, User
from career_copilot.settings import AuthSettings

LOCAL_ISSUER = "local"
IDENTITY_UNIQUE_CONSTRAINT = "uq_users_identity_issuer"


def authentication_error(detail: str = "authentication_required") -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


async def register(
    data: RegisterRequest, db: AsyncSession, passwords: PasswordService
) -> RegisterResponse:
    password_hash = await passwords.hash(data.password.get_secret_value())
    user = User(
        identity_issuer=LOCAL_ISSUER,
        identity_subject=data.username,
        status=UserStatus.ACTIVE,
    )
    try:
        async with db.begin():
            db.add(user)
            await db.flush()
            db.add(LocalCredential(user_id=user.id, password_hash=password_hash))
    except IntegrityError as error:
        if (
            getattr(error.orig, "sqlstate", None) == "23505"
            and getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            == IDENTITY_UNIQUE_CONSTRAINT
        ):
            raise HTTPException(409, "username_unavailable") from None
        raise
    return RegisterResponse(id=user.id, status=user.status, username=data.username)


async def login(
    data: LoginRequest,
    db: AsyncSession,
    passwords: PasswordService,
    tokens: TokenService,
    settings: AuthSettings,
) -> TokenResponse:
    query = (
        select(User, LocalCredential)
        .join(LocalCredential, LocalCredential.user_id == User.id)
        .where(
            User.identity_issuer == LOCAL_ISSUER,
            User.identity_subject == data.username,
        )
    )
    # Copy scalar verification material; no transaction is held during Argon2.
    async with db.begin():
        row = (await db.execute(query)).one_or_none()
        password_hash = row[1].password_hash if row else None
    if not await passwords.verify(data.password.get_secret_value(), password_hash):
        raise authentication_error("invalid_credentials")

    async with db.begin():
        # Refresh the identity map and lock both rows after the expensive verify.
        current = (
            await db.execute(
                query.with_for_update().execution_options(populate_existing=True)
            )
        ).one_or_none()
        if (
            current is None
            or current[0].status != UserStatus.ACTIVE
            or current[1].password_hash != password_hash
        ):
            raise authentication_error("invalid_credentials")
        issued_at = int(utc_now().timestamp())
        claims = TokenClaims(
            user_id=current[0].id,
            session_id=uuid4(),
            issued_at=issued_at,
            expires_at=issued_at + settings.session_ttl_seconds,
        )
        token = tokens.issue(claims)
        db.add(
            AuthSession(
                id=claims.session_id,
                user_id=claims.user_id,
                token_hash=hash_token(token),
                created_at=datetime.fromtimestamp(claims.issued_at, UTC),
                expires_at=datetime.fromtimestamp(claims.expires_at, UTC),
            )
        )
    return TokenResponse(
        access_token=token,
        expires_in=max(0, claims.expires_at - int(utc_now().timestamp())),
    )


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


async def authenticate(
    token: str,
    db: AsyncSession,
    tokens: TokenService,
    *,
    for_logout: bool = False,
) -> AuthContext:
    try:
        claims = tokens.validate(token)
    except InvalidAccessToken:
        raise authentication_error() from None
    query = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id == claims.session_id)
    )
    if for_logout:
        query = query.with_for_update()
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise authentication_error()
    session, user = row
    if (
        session.user_id != claims.user_id
        or not hmac.compare_digest(
            session.token_hash.encode("utf-8"), hash_token(token).encode("ascii")
        )
        or session.expires_at != datetime.fromtimestamp(claims.expires_at, UTC)
        or session.expires_at <= utc_now()
        or (not for_logout and session.revoked_at is not None)
        or (not for_logout and user.status != UserStatus.ACTIVE)
    ):
        raise authentication_error()
    return AuthContext(user=user, session=session)


async def logout(token: str, db: AsyncSession, tokens: TokenService) -> None:
    async with db.begin():
        context = await authenticate(token, db, tokens, for_logout=True)
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.id == context.session.id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )

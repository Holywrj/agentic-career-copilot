"""Only an explicit Bearer header can identify the current platform User."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from career_copilot.auth.service import authenticate, authentication_error
from career_copilot.db.models import User
from career_copilot.db.session import get_session

bearer = HTTPBearer(auto_error=False, bearerFormat="JWT")


def get_bearer_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or len(request.headers.getlist("authorization")) != 1:
        raise authentication_error()
    return credentials.credentials


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(get_bearer_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    async with db.begin():
        context = await authenticate(token, db, request.app.state.tokens)
    return context.user

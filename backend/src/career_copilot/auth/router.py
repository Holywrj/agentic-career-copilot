"""Small JSON authentication API with sanitized error boundaries."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from career_copilot.auth import service
from career_copilot.auth.dependencies import get_bearer_token, get_current_user
from career_copilot.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from career_copilot.db.models import User
from career_copilot.db.session import get_session


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # Pydantic's default error input/ctx can contain a raw password.
                return JSONResponse({"detail": "invalid_request"}, status_code=422)
            except (SQLAlchemyError, UnknownHashError):
                # Never expose SQL parameters or misclassify infrastructure as 401.
                return JSONResponse(
                    {"detail": "authentication_unavailable"}, status_code=503
                )

        return sanitized_handler


router = APIRouter(prefix="/auth", tags=["auth"], route_class=AuthRoute)
Database = Annotated[AsyncSession, Depends(get_session)]
BearerToken = Annotated[str, Depends(get_bearer_token)]


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(data: RegisterRequest, request: Request, db: Database):
    return await service.register(data, db, request.app.state.passwords)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request, db: Database):
    return await service.login(
        data,
        db,
        request.app.state.passwords,
        request.app.state.tokens,
        request.app.state.auth_settings,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]):
    return user


@router.post("/logout", status_code=204)
async def logout(token: BearerToken, request: Request, db: Database) -> Response:
    await service.logout(token, db, request.app.state.tokens)
    return Response(status_code=204)

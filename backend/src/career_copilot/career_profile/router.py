"""Authenticated singleton API with a small, private error boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from career_copilot.auth.dependencies import get_current_user
from career_copilot.career_profile import service
from career_copilot.career_profile.schemas import (
    CareerProfileCreate,
    CareerProfileResponse,
    CareerProfileUpdate,
)
from career_copilot.db.models import User
from career_copilot.db.session import get_session


class CareerProfileRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"detail": "invalid_request"}, status_code=422)
            except SQLAlchemyError:
                return JSONResponse(
                    {"detail": "career_profile_unavailable"}, status_code=503
                )

        return sanitized_handler


router = APIRouter(
    prefix="/career-profile", tags=["career-profile"], route_class=CareerProfileRoute
)
Database = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=CareerProfileResponse)
async def get(current_user: CurrentUser, db: Database):
    return await service.get(current_user.id, db)


@router.post("", response_model=CareerProfileResponse, status_code=201)
async def create(
    data: CareerProfileCreate,
    current_user: CurrentUser,
    db: Database,
    response: Response,
):
    profile = await service.create(current_user.id, data, db)
    response.headers["Location"] = "/career-profile"
    return profile


@router.put("", response_model=CareerProfileResponse)
async def replace(data: CareerProfileUpdate, current_user: CurrentUser, db: Database):
    return await service.replace(current_user.id, data, db)

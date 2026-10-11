"""Resume-local validation and infrastructure error boundaries."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from career_copilot.auth.dependencies import get_current_user
from career_copilot.db.models import User
from career_copilot.db.session import get_session
from career_copilot.resume import service
from career_copilot.resume.schemas import (
    ResumeErrorResponse,
    ResumeListResponse,
    ResumeResponse,
)
from career_copilot.resume.storage import (
    BinaryTooLarge,
    EmptyBinary,
    StorageUnavailable,
)
from career_copilot.resume.uploads import parse_upload, validate_metadata


class ResumeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except (RequestValidationError, EmptyBinary):
                return JSONResponse({"detail": "invalid_request"}, status_code=422)
            except BinaryTooLarge:
                return JSONResponse({"detail": "request_too_large"}, status_code=413)
            except StorageUnavailable:
                return JSONResponse(
                    {"detail": "resume_storage_unavailable"}, status_code=503
                )
            except SQLAlchemyError:
                return JSONResponse({"detail": "resume_unavailable"}, status_code=503)

        return sanitized_handler


router = APIRouter(prefix="/resumes", tags=["resumes"], route_class=ResumeRoute)
Database = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
ERRORS = {
    code: {"description": description, "model": ResumeErrorResponse}
    for code, description in {
        401: "authentication_required",
        404: "resume_not_found",
        409: "resume_in_use",
        413: "request_too_large",
        415: "unsupported_resume_type",
        422: "invalid_request",
        503: "resume_unavailable or resume_storage_unavailable",
    }.items()
}
EXTRACTION_ERRORS = {
    **{code: ERRORS[code] for code in (401, 404, 422, 503)},
    409: {"description": "resume_extraction_conflict", "model": ResumeErrorResponse},
}


@router.post(
    "",
    response_model=ResumeResponse,
    status_code=201,
    responses=ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "additionalProperties": False,
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload(request: Request, current_user: CurrentUser, db: Database):
    # No automatic File/Form body dependency: authenticate before consuming body.
    async with parse_upload(request) as file:
        filename = validate_metadata(file)
        resource = await service.create(
            current_user.id, file, filename, db, request.app.state.resume_storage
        )
    return JSONResponse(
        resource.model_dump(mode="json"),
        status_code=201,
        headers={"Location": f"/resumes/{resource.id}"},
    )


@router.get("", response_model=ResumeListResponse, responses=ERRORS)
async def list_resumes(
    current_user: CurrentUser,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
):
    return await service.list_owned(current_user.id, db, limit, offset)


@router.get("/{resume_id}", response_model=ResumeResponse, responses=ERRORS)
async def get(resume_id: UUID, current_user: CurrentUser, db: Database):
    return await service.get(current_user.id, resume_id, db)


@router.post(
    "/{resume_id}/extract", response_model=ResumeResponse, responses=EXTRACTION_ERRORS
)
async def extract(
    resume_id: UUID, request: Request, current_user: CurrentUser, db: Database
):
    # No body dependency or body consumption: this action has no input options.
    return await service.extract(
        current_user.id,
        resume_id,
        db,
        request.app.state.resume_storage,
        request.app.state.resume_extraction_limiter,
    )


@router.delete("/{resume_id}", status_code=204, responses=ERRORS)
async def delete(
    resume_id: UUID, request: Request, current_user: CurrentUser, db: Database
) -> Response:
    await service.remove(
        current_user.id, resume_id, db, request.app.state.resume_storage
    )
    return Response(status_code=204)

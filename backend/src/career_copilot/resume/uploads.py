"""Authenticated, bounded multipart parsing and basic PDF admission policy."""

import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException, MultiPartParser

from career_copilot.resume.storage import StorageUnavailable

MAX_RESUME_BYTES = 5 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
MAX_RESUME_REQUEST_BYTES = MAX_RESUME_BYTES + 64 * 1024
PDF_MEDIA_TYPE = "application/pdf"


class ResumeMultipartParser(MultiPartParser):
    """Require the closing boundary; python-multipart finalize is permissive."""

    def __init__(self, request: Request) -> None:
        self.completed = False
        super().__init__(
            request.headers, self.complete_stream(request), max_files=1, max_fields=0
        )

    def on_end(self) -> None:
        self.completed = True

    async def complete_stream(self, request: Request) -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk
        if not self.completed:
            # Raise inside the base parser's protected stream loop so it closes
            # every spool, including the unfinished file absent from FormData.
            raise MultiPartException("Invalid multipart data")


def validate_metadata(file: UploadFile) -> str:
    name = file.filename
    if (
        not name
        or not name.strip()
        or name in {".", ".."}
        or any(
            character in "/\\:" or unicodedata.category(character) in {"Cc", "Cs"}
            for character in name
        )
        or len(name.encode("utf-8")) > 255
    ):
        raise HTTPException(422, "invalid_request")
    if not name.lower().endswith(".pdf") or file.content_type != PDF_MEDIA_TYPE:
        raise HTTPException(415, "unsupported_resume_type")
    return name


@asynccontextmanager
async def parse_upload(request: Request) -> AsyncIterator[UploadFile]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "multipart/form-data"
    ):
        raise HTTPException(422, "invalid_request")
    try:
        form = await ResumeMultipartParser(request).parse()
        try:
            items = form.multi_items()
            if (
                len(items) != 1
                or items[0][0] != "file"
                or not isinstance(items[0][1], UploadFile)
            ):
                raise HTTPException(422, "invalid_request")
            yield items[0][1]
        finally:
            await form.close()
    except StarletteHTTPException as error:
        if error.status_code == 400:
            raise HTTPException(422, "invalid_request") from None
        raise
    except MultiPartException:
        raise HTTPException(422, "invalid_request") from None
    except OSError:
        raise StorageUnavailable() from None


async def pdf_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    # Fixed prefix read, never an unbounded read. This is admission checking,
    # not a PDF parser or a promise that extraction will succeed.
    prefix = await file.read(5)
    if not prefix:
        raise HTTPException(422, "invalid_request")
    if prefix != b"%PDF-":
        raise HTTPException(415, "unsupported_resume_type")
    yield prefix
    while chunk := await file.read(CHUNK_BYTES):
        yield chunk

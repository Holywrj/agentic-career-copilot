"""Explicit owner-scoped transactions and compensation for separate binary storage."""

import logging
import re
from uuid import UUID, uuid4

import anyio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from career_copilot.db.models import Resume
from career_copilot.resume import extraction
from career_copilot.resume.schemas import ResumeListResponse, ResumeResponse
from career_copilot.resume.storage import ResumeStorage, StorageUnavailable, complete
from career_copilot.resume.uploads import MAX_RESUME_BYTES, PDF_MEDIA_TYPE, pdf_chunks

logger = logging.getLogger(__name__)
TABLE = Resume.__table__
COLUMNS = (
    TABLE.c.id,
    TABLE.c.original_filename,
    TABLE.c.media_type,
    TABLE.c.size_bytes,
    TABLE.c.parsing_status.label("status"),
    TABLE.c.created_at,
    TABLE.c.updated_at,
)
DELETE_REFERENCES = {"fk_resumes_owner_previous", "fk_job_analyses_owner_resume"}
EXTRACTION_COLUMNS = (*COLUMNS, TABLE.c.storage_key, TABLE.c.extracted_text)


def cached_extraction(row) -> bool:
    status, text = row["status"], row["extracted_text"]
    if status == "extracted" and text is not None and text.strip():
        return True
    if status == "text_unavailable" and text is None:
        return True
    if status in {"uploaded", "extraction_failed"} and text is None:
        return False
    raise HTTPException(409, "resume_extraction_conflict")


def extraction_metadata(row) -> ResumeResponse:
    return ResumeResponse.model_validate(
        {column.key: row[column.key] for column in COLUMNS}
    )


async def extract(
    owner_id: UUID,
    resume_id: UUID,
    db: AsyncSession,
    storage: ResumeStorage,
    limiter: anyio.CapacityLimiter,
) -> ResumeResponse:
    async with db.begin():
        result = await db.execute(
            select(*EXTRACTION_COLUMNS).where(
                TABLE.c.user_id == owner_id, TABLE.c.id == resume_id
            )
        )
        snapshot = result.mappings().one_or_none()
        if snapshot is None:
            raise HTTPException(404, "resume_not_found")
        if cached_extraction(snapshot):
            return extraction_metadata(snapshot)

    try:
        # Bound complete read/parse pipelines, not only executing parser threads.
        # Waiting requests hold neither binary bytes nor a database connection.
        async with limiter:
            binary = await storage.read(snapshot["storage_key"], MAX_RESUME_BYTES)
            if (
                snapshot["size_bytes"] is not None
                and len(binary) != snapshot["size_bytes"]
            ):
                raise StorageUnavailable()
            outcome = await extraction.parse_binary(binary)
            # Do not retain input while waiting for the final database row lock.
            del binary
    except StorageUnavailable:
        # A racing DELETE may have removed metadata as well as binary. This
        # short read never repairs metadata or changes the extraction lifecycle.
        await get(owner_id, resume_id, db)
        logger.warning(
            "operation=extract resume_id=%s reason=storage_unavailable", resume_id
        )
        raise StorageUnavailable() from None

    await anyio.lowlevel.checkpoint()

    async def persist_and_finish() -> ResumeResponse:
        committing = False
        try:
            async with db.begin():
                result = await db.execute(
                    select(*EXTRACTION_COLUMNS)
                    .where(TABLE.c.user_id == owner_id, TABLE.c.id == resume_id)
                    .with_for_update()
                )
                row = result.mappings().one_or_none()
                if row is None:
                    raise HTTPException(404, "resume_not_found")
                if row["storage_key"] != snapshot["storage_key"]:
                    raise HTTPException(409, "resume_extraction_conflict")
                if cached_extraction(row):
                    return extraction_metadata(row)
                result = await db.execute(
                    update(TABLE)
                    .where(
                        TABLE.c.user_id == owner_id,
                        TABLE.c.id == resume_id,
                        TABLE.c.storage_key == snapshot["storage_key"],
                    )
                    .values(
                        parsing_status=outcome.status,
                        extracted_text=outcome.text,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(*COLUMNS)
                )
                response = ResumeResponse.model_validate(dict(result.mappings().one()))
                committing = True
            if outcome.reason is not None:
                logger.info(
                    "operation=extract resume_id=%s reason=%s",
                    resume_id,
                    outcome.reason,
                )
            return response
        except SQLAlchemyError as error:
            if committing and not isinstance(error, IntegrityError):
                logger.error(
                    "operation=extract resume_id=%s reason=db_commit_uncertain",
                    resume_id,
                )
            raise

    # Sole ownership of the session belongs to this child through transaction
    # exit. Cancellation cannot close the request session midway through commit.
    return await complete(persist_and_finish())


async def create(
    owner_id: UUID,
    file: UploadFile,
    filename: str,
    db: AsyncSession,
    storage: ResumeStorage,
) -> ResumeResponse:
    resume_id, key = uuid4(), uuid4().hex
    binary = await storage.save(key, pdf_chunks(file), MAX_RESUME_BYTES)
    committing = False
    try:
        async with db.begin():
            result = await db.execute(
                TABLE.insert()
                .values(
                    id=resume_id,
                    user_id=owner_id,
                    original_filename=filename,
                    storage_key=key,
                    media_type=PDF_MEDIA_TYPE,
                    size_bytes=binary.size_bytes,
                    parsing_status="uploaded",
                )
                .returning(*COLUMNS)
            )
            response = ResumeResponse.model_validate(dict(result.mappings().one()))
            committing = True
        return response
    except BaseException as error:
        # COMMIT transport/cancellation failures have an unknown outcome. An
        # IntegrityError is a server-confirmed rejection; earlier failures never
        # sent COMMIT. Keep binary on uncertainty rather than risk a broken row.
        if committing and not isinstance(error, IntegrityError):
            logger.error("resume_commit_uncertain operation=upload key=%s", key)
        else:
            try:
                await complete(storage.delete(key))
            except StorageUnavailable:
                logger.error("resume_compensation_failed operation=upload key=%s", key)
                raise
        raise


async def list_owned(
    owner_id: UUID, db: AsyncSession, limit: int, offset: int
) -> ResumeListResponse:
    async with db.begin():
        result = await db.execute(
            select(*COLUMNS)
            .where(TABLE.c.user_id == owner_id)
            .order_by(TABLE.c.created_at.desc(), TABLE.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return ResumeListResponse(
            items=[
                ResumeResponse.model_validate(dict(row)) for row in result.mappings()
            ],
            limit=limit,
            offset=offset,
        )


async def get(owner_id: UUID, resume_id: UUID, db: AsyncSession) -> ResumeResponse:
    async with db.begin():
        result = await db.execute(
            select(*COLUMNS).where(TABLE.c.user_id == owner_id, TABLE.c.id == resume_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "resume_not_found")
        return ResumeResponse.model_validate(dict(row))


async def remove(
    owner_id: UUID, resume_id: UUID, db: AsyncSession, storage: ResumeStorage
) -> None:
    transaction = await db.begin()
    try:
        key = await db.scalar(
            select(TABLE.c.storage_key)
            .where(TABLE.c.user_id == owner_id, TABLE.c.id == resume_id)
            .with_for_update()
        )
        if key is None:
            raise HTTPException(404, "resume_not_found")
        try:
            # Execute before unlink so existing FK restrictions preserve binary.
            await db.execute(
                delete(TABLE).where(
                    TABLE.c.user_id == owner_id, TABLE.c.id == resume_id
                )
            )
        except IntegrityError as error:
            if (
                getattr(error.orig, "sqlstate", None) == "23503"
                and getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                in DELETE_REFERENCES
            ):
                raise HTTPException(409, "resume_in_use") from None
            raise
        # Honor cancellation while binary is still untouched, before handing
        # sole session/transaction ownership to the protected operation below.
        await anyio.lowlevel.checkpoint()
    except BaseException:
        await complete(transaction.rollback())
        raise

    async def delete_and_finish() -> None:
        try:
            try:
                await storage.delete(key)
            except StorageUnavailable:
                logger.error(
                    "resume_storage_delete_failed operation=delete key=%s",
                    key if re.fullmatch(r"[0-9a-f]{32}", key) else "invalid",
                )
                raise
            await transaction.commit()
        except BaseException:
            if db.in_transaction():
                await transaction.rollback()
            raise

    # Cancellation must wait for both unlink and transaction completion. There
    # is no outer transaction context to roll back a successful child commit;
    # the request dependency can close the session only after this child ends.
    await complete(delete_and_finish())

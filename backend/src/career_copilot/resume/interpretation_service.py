"""Owner snapshots, DB-free model waits, and serialized acceptance of results."""

import hashlib
import logging
from uuid import UUID

import anyio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from career_copilot.db.models import Resume
from career_copilot.resume.interpretation_schemas import (
    SCHEMA_VERSION,
    PersistedResumeInterpretation,
    ResumeInterpretationResponse,
    StructuredResumeV1,
)
from career_copilot.resume.interpreter import ResumeInterpreter
from career_copilot.resume.storage import complete

logger = logging.getLogger(__name__)
MAX_INTERPRETATION_INPUT_BYTES = 32 * 1024
TABLE = Resume.__table__
COLUMNS = (
    TABLE.c.id,
    TABLE.c.storage_key,
    TABLE.c.parsing_status,
    TABLE.c.extracted_text,
    TABLE.c.structured_content,
)


def source_digest(row, *, final: bool = False) -> str:
    text = row["extracted_text"]
    if row["parsing_status"] != "extracted" or not text or not text.strip():
        detail = "conflict" if final else "not_ready"
        raise HTTPException(409, f"resume_interpretation_{detail}")
    try:
        binary = text.encode("utf-8")
    except UnicodeError:
        raise HTTPException(409, "resume_interpretation_conflict") from None
    if len(binary) > MAX_INTERPRETATION_INPUT_BYTES:
        raise HTTPException(413, "resume_interpretation_input_too_large")
    return hashlib.sha256(binary).hexdigest()


def persisted(row, digest: str) -> PersistedResumeInterpretation | None:
    if row["structured_content"] is None:
        return None
    try:
        result = PersistedResumeInterpretation.model_validate(row["structured_content"])
    except (ValidationError, ValueError):
        raise HTTPException(409, "resume_interpretation_conflict") from None
    if result.source_sha256 != digest:
        raise HTTPException(409, "resume_interpretation_conflict")
    return result


def response(resume_id: UUID, result: PersistedResumeInterpretation):
    return ResumeInterpretationResponse(
        resume_id=resume_id,
        schema_version=result.schema_version,
        model_identifier=result.model_identifier,
        content=result.content,
    )


async def lookup(owner_id: UUID, resume_id: UUID, db: AsyncSession, *, lock=False):
    statement = select(*COLUMNS).where(
        TABLE.c.user_id == owner_id, TABLE.c.id == resume_id
    )
    if lock:
        statement = statement.with_for_update()
    row = (await db.execute(statement)).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "resume_not_found")
    return row


async def get(owner_id: UUID, resume_id: UUID, db: AsyncSession):
    async with db.begin():
        row = await lookup(owner_id, resume_id, db)
        result = persisted(row, source_digest(row))
        if result is None:
            raise HTTPException(404, "resume_interpretation_not_found")
        return response(resume_id, result)


async def interpret(
    owner_id: UUID,
    resume_id: UUID,
    db: AsyncSession,
    interpreter: ResumeInterpreter,
    limiter: anyio.CapacityLimiter,
) -> ResumeInterpretationResponse:
    async with db.begin():
        snapshot = await lookup(owner_id, resume_id, db)
        digest = source_digest(snapshot)
        cached = persisted(snapshot, digest)

    try:
        model_identifier = interpreter.model_identifier
    except Exception:
        logger.warning(
            "operation=interpret resume_id=%s reason=model_unavailable", resume_id
        )
        raise HTTPException(503, "resume_interpretation_unavailable") from None
    if cached is not None:
        if cached.model_identifier != model_identifier:
            raise HTTPException(409, "resume_interpretation_conflict")
        return response(resume_id, cached)

    # Snapshot transaction exited: no checked-out connection, row lock, or
    # transaction while waiting for capacity or the request-owned async LLM call.
    try:
        async with limiter:
            output = await interpreter.interpret(snapshot["extracted_text"])
    except Exception:
        logger.warning(
            "operation=interpret resume_id=%s reason=model_unavailable", resume_id
        )
        raise HTTPException(503, "resume_interpretation_unavailable") from None
    try:
        # Revalidate even Pydantic instances returned by an injected interpreter.
        content = StructuredResumeV1.model_validate(output)
        accepted = PersistedResumeInterpretation(
            schema_version=SCHEMA_VERSION,
            model_identifier=model_identifier,
            source_sha256=digest,
            content=content,
        )
    except (ValidationError, ValueError):
        logger.warning(
            "operation=interpret resume_id=%s reason=invalid_output", resume_id
        )
        raise HTTPException(503, "resume_interpretation_unavailable") from None

    # Both AnyIO scope cancellation and asyncio task cancellation must be
    # observed before transferring sole session ownership to the final writer.
    await anyio.lowlevel.checkpoint()

    async def persist_and_finish():
        committing = False
        try:
            async with db.begin():
                row = await lookup(owner_id, resume_id, db, lock=True)
                if (
                    row["storage_key"] != snapshot["storage_key"]
                    or row["extracted_text"] != snapshot["extracted_text"]
                    or row["parsing_status"] != snapshot["parsing_status"]
                ):
                    raise HTTPException(409, "resume_interpretation_conflict")
                current = persisted(row, source_digest(row, final=True))
                if current is not None:
                    if current.model_identifier != model_identifier:
                        raise HTTPException(409, "resume_interpretation_conflict")
                    return response(resume_id, current)
                await db.execute(
                    update(TABLE)
                    .where(TABLE.c.user_id == owner_id, TABLE.c.id == resume_id)
                    .values(
                        structured_content=accepted.model_dump(mode="json"),
                        updated_at=func.clock_timestamp(),
                    )
                )
                result = response(resume_id, accepted)
                committing = True
            return result
        except SQLAlchemyError as error:
            if committing and not isinstance(error, IntegrityError):
                logger.error(
                    "operation=interpret resume_id=%s reason=db_commit_uncertain",
                    resume_id,
                )
            raise

    # Reuse the established transaction ownership boundary; this protects only
    # commit/rollback, never the model call. No work outlives the request cleanup.
    return await complete(persist_and_finish())

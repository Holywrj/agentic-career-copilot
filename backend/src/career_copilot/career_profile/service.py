"""Owner-scoped reads and atomic writes with explicit short transactions."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Numeric, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from career_copilot.career_profile.schemas import (
    CareerProfileCreate,
    CareerProfileResponse,
    CareerProfileUpdate,
)
from career_copilot.db.models import CareerProfile

PROFILE = CareerProfile.__table__
RESPONSE_COLUMNS = tuple(PROFILE.c[name] for name in CareerProfileResponse.model_fields)


async def get(owner_id: UUID, db: AsyncSession) -> CareerProfileResponse:
    async with db.begin():
        result = await db.execute(
            select(*RESPONSE_COLUMNS).where(PROFILE.c.user_id == owner_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "career_profile_not_found")
        response = CareerProfileResponse.model_validate(dict(row))
    return response


async def create(
    owner_id: UUID, data: CareerProfileCreate, db: AsyncSession
) -> CareerProfileResponse:
    async with db.begin():
        result = await db.execute(
            insert(PROFILE)
            .values(user_id=owner_id, **data.model_dump())
            .on_conflict_do_nothing(index_elements=[PROFILE.c.user_id])
            .returning(*RESPONSE_COLUMNS)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(409, "career_profile_already_exists")
        response = CareerProfileResponse.model_validate(dict(row))
    return response


async def replace(
    owner_id: UUID, data: CareerProfileUpdate, db: AsyncSession
) -> CareerProfileResponse:
    async with db.begin():
        result = await db.execute(
            update(PROFILE)
            .where(
                PROFILE.c.user_id == owner_id,
                # Out-of-range positive input revisions simply cannot match int32.
                PROFILE.c.revision == literal(data.expected_revision, type_=Numeric()),
            )
            .values(
                **data.model_dump(exclude={"expected_revision"}),
                revision=PROFILE.c.revision + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(*RESPONSE_COLUMNS)
        )
        row = result.mappings().one_or_none()
        if row is None:
            exists = await db.scalar(
                select(PROFILE.c.user_id).where(PROFILE.c.user_id == owner_id)
            )
            if exists is None:
                raise HTTPException(404, "career_profile_not_found")
            raise HTTPException(409, "career_profile_conflict")
        response = CareerProfileResponse.model_validate(dict(row))
    return response

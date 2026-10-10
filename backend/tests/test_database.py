import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from career_copilot.db.models import (
    AuthSession,
    CareerProfile,
    Job,
    JobAnalysis,
    Resume,
    User,
)
from career_copilot.db.session import create_session_factory

pytestmark = pytest.mark.database


def new_user() -> User:
    return User(
        identity_issuer="https://identity.example.test", identity_subject=uuid4().hex
    )


async def sources(session: AsyncSession) -> tuple[User, Resume, Job]:
    user = new_user()
    session.add(user)
    await session.flush()
    resume = Resume(
        user_id=user.id, original_filename="resume.pdf", storage_key=uuid4().hex
    )
    job = Job(user_id=user.id, description="Python backend engineer")
    session.add_all([resume, job])
    await session.flush()
    return user, resume, job


def analysis(user_id: UUID, resume_id: UUID, job_id: UUID, **values) -> JobAnalysis:
    return JobAnalysis(
        user_id=user_id,
        resume_id=resume_id,
        job_id=job_id,
        profile_revision=1,
        input_snapshot={"profile": {"skills": []}, "resume": {}, "job": {}},
        input_schema_version=1,
        analysis_version="fixture-v1",
        **values,
    )


async def rejected(session: AsyncSession, record, sqlstate: str) -> None:
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    assert error.value.orig.sqlstate == sqlstate


async def test_optional_profile_multiple_versions_and_analyses(
    db_engine: AsyncEngine,
) -> None:
    factory = create_session_factory(db_engine)
    async with factory.begin() as session:
        user, resume, job = await sources(session)
        assert await session.get(CareerProfile, user.id) is None
        session.add(
            Resume(
                user_id=user.id,
                supersedes_resume_id=resume.id,
                original_filename="targeted.pdf",
                storage_key=uuid4().hex,
            )
        )
        session.add_all(
            [analysis(user.id, resume.id, job.id), analysis(user.id, resume.id, job.id)]
        )
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Resume)) == 2
        assert await session.scalar(select(func.count()).select_from(JobAnalysis)) == 2


async def test_identity_unique_and_required_fields(db_engine: AsyncEngine) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, _, _ = await sources(session)
        await rejected(
            session,
            User(
                identity_issuer=user.identity_issuer,
                identity_subject=user.identity_subject,
            ),
            "23505",
        )
        await rejected(session, User(identity_issuer=user.identity_issuer), "23502")
        await rejected(
            session,
            User(
                identity_issuer="issuer", identity_subject="subject", status="unknown"
            ),
            "23514",
        )
        # The same subject from a different issuer represents a distinct identity.
        session.add(
            User(identity_issuer="other-issuer", identity_subject=user.identity_subject)
        )


async def test_profile_unique_fk_revision_and_json_shape(
    db_engine: AsyncEngine,
) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, _, _ = await sources(session)
        session.add(CareerProfile(user_id=user.id))
        await session.flush()
        # Use a separate session to avoid a duplicate identity-map warning.
        other = new_user()
        session.add(other)
        await session.flush()
        await rejected(session, CareerProfile(user_id=uuid4()), "23503")
        await rejected(session, CareerProfile(user_id=other.id, revision=0), "23514")
        await rejected(session, CareerProfile(user_id=other.id, skills={}), "23514")
        user_id = user.id
    async with create_session_factory(db_engine).begin() as session:
        await rejected(session, CareerProfile(user_id=user_id), "23505")


@pytest.mark.parametrize(
    "reference", ["job", "resume", "missing_job", "missing_resume"]
)
async def test_analysis_rejects_cross_owner_and_missing_sources(
    db_engine: AsyncEngine,
    reference: str,
) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, resume, job = await sources(session)
        _, other_resume, other_job = await sources(session)
        job_id = other_job.id if reference == "job" else job.id
        resume_id = other_resume.id if reference == "resume" else resume.id
        if reference == "missing_job":
            job_id = uuid4()
        if reference == "missing_resume":
            resume_id = uuid4()
        await rejected(session, analysis(user.id, resume_id, job_id), "23503")


async def test_version_lineage_is_owned_and_not_self_referential(
    db_engine: AsyncEngine,
) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, _, _ = await sources(session)
        _, other_resume, _ = await sources(session)
        for previous in [other_resume.id, uuid4()]:
            await rejected(
                session,
                Resume(
                    user_id=user.id,
                    supersedes_resume_id=previous,
                    original_filename="resume.pdf",
                    storage_key=uuid4().hex,
                ),
                "23503",
            )
        own_id = uuid4()
        await rejected(
            session,
            Resume(
                id=own_id,
                user_id=user.id,
                supersedes_resume_id=own_id,
                original_filename="resume.pdf",
                storage_key=uuid4().hex,
            ),
            "23514",
        )


async def test_analysis_results_and_status_constraints(db_engine: AsyncEngine) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, resume, job = await sources(session)
        invalid = [
            {"status": "unknown"},
            {"status": "succeeded"},
            {"status": "failed"},
            {"result": {}, "result_schema_version": None},
            {"result": {}, "result_schema_version": 0},
            {"result": [], "result_schema_version": 1},
        ]
        for values in invalid:
            await rejected(
                session, analysis(user.id, resume.id, job.id, **values), "23514"
            )
        record = analysis(
            user.id,
            resume.id,
            job.id,
            status="succeeded",
            result={},
            result_schema_version=1,
            model_identifier="fixture-model",
            completed_at=datetime.now(UTC),
        )
        session.add(record)
        await session.flush()
        record_id = record.id
    async with create_session_factory(db_engine).begin() as session:
        loaded = await session.get(JobAnalysis, record_id)
        assert loaded.result == {}
        # Input history does not depend on the user's current Profile.
        session.add(CareerProfile(user_id=user.id, revision=2, skills=["new skill"]))
    async with create_session_factory(db_engine)() as session:
        loaded = await session.get(JobAnalysis, record_id)
        assert loaded.profile_revision == 1
        assert loaded.input_snapshot["profile"]["skills"] == []


async def test_job_description_and_required_references(db_engine: AsyncEngine) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, resume, job = await sources(session)
        for description in ["", " \t\n"]:
            await rejected(
                session, Job(user_id=user.id, description=description), "23514"
            )
        await rejected(session, Job(description="Missing owner"), "23502")
        await rejected(session, analysis(user.id, None, job.id), "23502")
        await rejected(session, analysis(user.id, resume.id, None), "23502")


async def test_auth_sessions_unique_owned_and_expiring(db_engine: AsyncEngine) -> None:
    async with create_session_factory(db_engine).begin() as session:
        user, _, _ = await sources(session)
        expires = datetime.now(UTC) + timedelta(hours=1)
        record = AuthSession(
            user_id=user.id, token_hash="fixture-hash", expires_at=expires
        )
        session.add(record)
        await session.flush()
        assert record.revoked_at is None
        await rejected(
            session,
            AuthSession(user_id=user.id, token_hash="fixture-hash", expires_at=expires),
            "23505",
        )
        await rejected(
            session,
            AuthSession(user_id=uuid4(), token_hash="other-hash", expires_at=expires),
            "23503",
        )
        await rejected(
            session,
            AuthSession(
                user_id=user.id,
                token_hash="past-hash",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            ),
            "23514",
        )


async def test_commit_rollback_and_no_partial_writes(db_engine: AsyncEngine) -> None:
    factory = create_session_factory(db_engine)
    committed = new_user()
    async with factory.begin() as session:
        session.add(committed)
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            session.add(new_user())
            await session.flush()
            session.add(
                User(
                    identity_issuer=committed.identity_issuer,
                    identity_subject=committed.identity_subject,
                )
            )
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
    # Closing a session with flushed but uncommitted data must roll it back.
    async with factory() as session:
        session.add(new_user())
        await session.flush()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_concurrent_tasks_have_independent_sessions(
    db_engine: AsyncEngine,
) -> None:
    factory = create_session_factory(db_engine)

    async def insert() -> tuple[AsyncSession, UUID]:
        async with factory.begin() as session:
            user = new_user()
            session.add(user)
            await session.flush()
            return session, user.id

    results = await asyncio.gather(*(insert() for _ in range(4)))
    assert len({id(session) for session, _ in results}) == 4
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 4


async def test_timestamps_and_restrict_delete(db_engine: AsyncEngine) -> None:
    factory = create_session_factory(db_engine)
    async with factory.begin() as session:
        user, resume, _ = await sources(session)
        assert user.created_at.tzinfo is not None
        assert resume.created_at.tzinfo is not None
        previous = user.updated_at
        user_id = user.id
    async with factory.begin() as session:
        user = await session.get(User, user_id)
        user.status = "disabled"
        await session.flush()
        await session.refresh(user)
        assert user.updated_at > previous
        with pytest.raises(IntegrityError) as error:
            async with session.begin_nested():
                await session.delete(user)
                await session.flush()
        assert error.value.orig.sqlstate == "23503"

"""First-phase domain records and authentication session infrastructure."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from career_copilot.db.base import Base, CreatedAt, Timestamps


class User(Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("identity_issuer", "identity_subject"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_issuer: Mapped[str] = mapped_column(Text)
    identity_subject: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)


class LocalCredential(CreatedAt, Base):
    __tablename__ = "local_credentials"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)


class CareerProfile(Timestamps, Base):
    __tablename__ = "career_profiles"
    __table_args__ = (
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint("jsonb_typeof(skills) = 'array'", name="skills_array"),
        CheckConstraint(
            "jsonb_typeof(target_roles) = 'array'", name="target_roles_array"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(server_default="1")
    career_summary: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    target_roles: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class Resume(Timestamps, Base):
    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(
            ["user_id", "supersedes_resume_id"],
            ["resumes.user_id", "resumes.id"],
            name="fk_resumes_owner_previous",
        ),
        CheckConstraint("supersedes_resume_id <> id", name="not_self_version"),
        CheckConstraint(
            "structured_content IS NULL OR jsonb_typeof(structured_content) = 'object'",
            name="structured_content_object",
        ),
        Index("ix_resumes_owner_created", "user_id", "created_at"),
        Index("ix_resumes_owner_previous", "user_id", "supersedes_resume_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    supersedes_resume_id: Mapped[UUID | None] = mapped_column()
    original_filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    parsing_status: Mapped[str] = mapped_column(Text)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    structured_content: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )


class Job(Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        CheckConstraint("description ~ '[^[:space:]]'", name="description_not_blank"),
        Index("ix_jobs_owner_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)


class JobAnalysis(Timestamps, Base):
    __tablename__ = "job_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "job_id"],
            ["jobs.user_id", "jobs.id"],
            name="fk_job_analyses_owner_job",
        ),
        ForeignKeyConstraint(
            ["user_id", "resume_id"],
            ["resumes.user_id", "resumes.id"],
            name="fk_job_analyses_owner_resume",
        ),
        CheckConstraint("profile_revision > 0", name="positive_profile_revision"),
        CheckConstraint("input_schema_version > 0", name="positive_input_version"),
        CheckConstraint("result_schema_version > 0", name="positive_result_version"),
        CheckConstraint("jsonb_typeof(input_snapshot) = 'object'", name="input_object"),
        CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'", name="result_object"
        ),
        CheckConstraint(
            "(result IS NULL) = (result_schema_version IS NULL)", name="result_version"
        ),
        Index("ix_job_analyses_owner_created", "user_id", "created_at"),
        Index("ix_job_analyses_owner_job_created", "user_id", "job_id", "created_at"),
        Index("ix_job_analyses_owner_resume", "user_id", "resume_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[UUID] = mapped_column()
    resume_id: Mapped[UUID] = mapped_column()
    profile_revision: Mapped[int] = mapped_column()
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_schema_version: Mapped[int] = mapped_column()
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    result_schema_version: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    model_identifier: Mapped[str | None] = mapped_column(Text)
    analysis_version: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(CreatedAt, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

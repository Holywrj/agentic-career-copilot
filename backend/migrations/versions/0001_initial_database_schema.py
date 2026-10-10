"""initial database schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_issuer", sa.Text(), nullable=False),
        sa.Column("identity_subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_users_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint(
            "identity_issuer", "identity_subject", name=op.f("uq_users_identity_issuer")
        ),
    )
    op.create_table(
        "career_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("career_summary", sa.Text(), nullable=True),
        sa.Column(
            "skills",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "target_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(skills) = 'array'",
            name=op.f("ck_career_profiles_skills_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(target_roles) = 'array'",
            name=op.f("ck_career_profiles_target_roles_array"),
        ),
        sa.CheckConstraint(
            "revision > 0", name=op.f("ck_career_profiles_positive_revision")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_career_profiles_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_career_profiles")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "description ~ '[^[:space:]]'", name=op.f("ck_jobs_description_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_jobs_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_jobs_user_id")),
    )
    op.create_index(
        "ix_jobs_owner_created", "jobs", ["user_id", "created_at"], unique=False
    )
    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_resume_id", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "parsing_status", sa.Text(), server_default="pending", nullable=False
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "structured_content",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "parsing_status IN ('pending', 'parsing', 'ready', 'failed')",
            name=op.f("ck_resumes_parsing_status"),
        ),
        sa.CheckConstraint(
            "structured_content IS NULL OR jsonb_typeof(structured_content) = 'object'",
            name=op.f("ck_resumes_structured_content_object"),
        ),
        sa.CheckConstraint(
            "supersedes_resume_id <> id", name=op.f("ck_resumes_not_self_version")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "supersedes_resume_id"],
            ["resumes.user_id", "resumes.id"],
            name="fk_resumes_owner_previous",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_resumes_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resumes")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_resumes_user_id")),
    )
    op.create_index(
        "ix_resumes_owner_created", "resumes", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_resumes_owner_previous",
        "resumes",
        ["user_id", "supersedes_resume_id"],
        unique=False,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_sessions_expires_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_table(
        "job_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column(
            "input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("input_schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("result_schema_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("model_identifier", sa.Text(), nullable=True),
        sa.Column("analysis_version", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name=op.f("ck_job_analyses_input_object"),
        ),
        sa.CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'",
            name=op.f("ck_job_analyses_result_object"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (result IS NOT NULL AND "
            "completed_at IS NOT NULL AND model_identifier IS NOT NULL)",
            name=op.f("ck_job_analyses_success_fields"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name=op.f("ck_job_analyses_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR completed_at IS NOT NULL",
            name=op.f("ck_job_analyses_terminal_completion"),
        ),
        sa.CheckConstraint(
            "(result IS NULL) = (result_schema_version IS NULL)",
            name=op.f("ck_job_analyses_result_version"),
        ),
        sa.CheckConstraint(
            "input_schema_version > 0",
            name=op.f("ck_job_analyses_positive_input_version"),
        ),
        sa.CheckConstraint(
            "profile_revision > 0",
            name=op.f("ck_job_analyses_positive_profile_revision"),
        ),
        sa.CheckConstraint(
            "result_schema_version > 0",
            name=op.f("ck_job_analyses_positive_result_version"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "job_id"],
            ["jobs.user_id", "jobs.id"],
            name="fk_job_analyses_owner_job",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "resume_id"],
            ["resumes.user_id", "resumes.id"],
            name="fk_job_analyses_owner_resume",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_job_analyses_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_analyses")),
    )
    op.create_index(
        "ix_job_analyses_owner_created",
        "job_analyses",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_analyses_owner_job_created",
        "job_analyses",
        ["user_id", "job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_analyses_owner_resume",
        "job_analyses",
        ["user_id", "resume_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_analyses_owner_resume", table_name="job_analyses")
    op.drop_index("ix_job_analyses_owner_job_created", table_name="job_analyses")
    op.drop_index("ix_job_analyses_owner_created", table_name="job_analyses")
    op.drop_table("job_analyses")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_resumes_owner_previous", table_name="resumes")
    op.drop_index("ix_resumes_owner_created", table_name="resumes")
    op.drop_table("resumes")
    op.drop_index("ix_jobs_owner_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("career_profiles")
    op.drop_table("users")

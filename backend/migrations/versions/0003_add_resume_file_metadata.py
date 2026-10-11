"""add resume file metadata and unique storage identity

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("media_type", sa.Text(), nullable=True))
    op.add_column("resumes", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_resumes_positive_size"),
        "resumes",
        "size_bytes IS NULL OR size_bytes > 0",
    )
    op.create_unique_constraint(
        op.f("uq_resumes_storage_key"), "resumes", ["storage_key"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_resumes_storage_key"), "resumes", type_="unique")
    op.drop_constraint(op.f("ck_resumes_positive_size"), "resumes", type_="check")
    op.drop_column("resumes", "size_bytes")
    op.drop_column("resumes", "media_type")

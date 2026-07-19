"""index scheduled execution retention cleanup

Revision ID: 20260719_0007
Revises: 20260719_0006
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_0007"
down_revision: str | None = "20260719_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_scheduled_job_execution_created",
        "scheduled_job_execution",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_scheduled_job_execution_created",
        table_name="scheduled_job_execution",
    )

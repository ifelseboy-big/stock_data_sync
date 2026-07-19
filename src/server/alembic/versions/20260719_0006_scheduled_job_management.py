"""add scheduled job management and execution records

Revision ID: 20260719_0006
Revises: 20260719_0005
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260719_0006"
down_revision: str | None = "20260719_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_job_control",
        sa.Column("job_id", sa.String(length=96), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("job_id", name="pk_scheduled_job_control"),
    )
    op.create_table(
        "scheduled_job_execution",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(length=96), nullable=False),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL', 'STARTUP_CATCHUP')",
            name="ck_scheduled_job_execution_scheduled_job_execution_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')",
            name="ck_scheduled_job_execution_scheduled_job_execution_status",
        ),
        sa.PrimaryKeyConstraint("execution_id", name="pk_scheduled_job_execution"),
    )
    op.create_index(
        "idx_scheduled_job_execution_job_time",
        "scheduled_job_execution",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_scheduled_job_execution_pending",
        "scheduled_job_execution",
        ["created_at", "execution_id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("idx_scheduled_job_execution_pending", table_name="scheduled_job_execution")
    op.drop_index("idx_scheduled_job_execution_job_time", table_name="scheduled_job_execution")
    op.drop_table("scheduled_job_execution")
    op.drop_table("scheduled_job_control")

"""add persistent provider request observations

Revision ID: 20260719_0003
Revises: 20260719_0002
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260719_0003"
down_revision: str | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_request_log",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("rate_limit_wait_ms", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="ck_provider_request_log_provider_request_duration",
        ),
        sa.CheckConstraint(
            "rate_limit_wait_ms >= 0",
            name="ck_provider_request_log_provider_request_wait",
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="ck_provider_request_log_provider_request_rows",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS', 'ERROR')",
            name="ck_provider_request_log_provider_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["collection_task.task_id"],
            name="fk_provider_request_log_task_id_collection_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_provider_request_log"),
    )
    op.create_index(
        "idx_provider_request_endpoint_time",
        "provider_request_log",
        ["provider", "endpoint", "requested_at"],
        unique=False,
    )
    op.create_index(
        "idx_provider_request_status_time",
        "provider_request_log",
        ["status", "requested_at"],
        unique=False,
    )
    op.create_index(
        "idx_provider_request_task",
        "provider_request_log",
        ["task_id", "requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_provider_request_task", table_name="provider_request_log")
    op.drop_index("idx_provider_request_status_time", table_name="provider_request_log")
    op.drop_index("idx_provider_request_endpoint_time", table_name="provider_request_log")
    op.drop_table("provider_request_log")

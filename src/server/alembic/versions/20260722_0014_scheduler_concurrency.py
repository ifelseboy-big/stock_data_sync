"""bound processing planning and serialize scheduler job executions

Revision ID: 20260722_0014
Revises: 20260722_0013
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260722_0014"
down_revision: str | None = "20260722_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_task",
        sa.Column("execution_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "processing_task",
        sa.Column("execution_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "collection_batch",
        sa.Column("processing_plan_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "collection_batch",
        sa.Column("processing_planned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_collection_batch_processing_plan",
        "collection_batch",
        [
            sa.text("processing_plan_version ASC NULLS FIRST"),
            sa.text("closed_at DESC NULLS LAST"),
            "batch_id",
        ],
        unique=False,
        postgresql_where=sa.text("status = 'CLOSED'"),
    )

    # A stopped scheduler cannot finish its RUNNING rows. Close them before the
    # uniqueness guard is installed so an upgrade never preserves false activity.
    op.execute(
        sa.text(
            "UPDATE scheduled_job_execution "
            "SET status = 'FAILED', finished_at = CURRENT_TIMESTAMP, "
            "duration_ms = LEAST(2147483647, GREATEST(0, (EXTRACT(EPOCH FROM "
            "(CURRENT_TIMESTAMP - COALESCE(started_at, created_at))) * 1000)::bigint)), "
            "error_message = 'scheduler process stopped before execution completed' "
            "WHERE status = 'RUNNING'"
        )
    )
    op.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT execution_id, row_number() OVER ("
            "PARTITION BY job_id ORDER BY created_at, execution_id) AS position "
            "FROM scheduled_job_execution WHERE status = 'PENDING'"
            ") UPDATE scheduled_job_execution AS execution "
            "SET status = 'FAILED', finished_at = CURRENT_TIMESTAMP, duration_ms = 0, "
            "error_message = 'duplicate pending scheduler request removed during upgrade' "
            "FROM ranked WHERE execution.execution_id = ranked.execution_id "
            "AND ranked.position > 1"
        )
    )
    op.create_index(
        "uq_scheduled_job_execution_running",
        "scheduled_job_execution",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "uq_scheduled_job_execution_pending_job",
        "scheduled_job_execution",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scheduled_job_execution_pending_job",
        table_name="scheduled_job_execution",
    )
    op.drop_index(
        "uq_scheduled_job_execution_running",
        table_name="scheduled_job_execution",
    )
    op.drop_index(
        "idx_collection_batch_processing_plan",
        table_name="collection_batch",
    )
    op.drop_column("collection_batch", "processing_planned_at")
    op.drop_column("collection_batch", "processing_plan_version")
    op.drop_column("processing_task", "execution_token")
    op.drop_column("collection_task", "execution_token")

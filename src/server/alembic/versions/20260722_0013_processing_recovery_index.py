"""index recovery lookups and collection queue claims

Revision ID: 20260722_0013
Revises: 20260721_0012
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260722_0013"
down_revision: str | None = "20260721_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_collection_batch_active_claim",
        "collection_batch",
        [
            sa.text(
                "(CASE WHEN batch_type = 'REPAIR' THEN 50 "
                "WHEN batch_type IN ('DAILY', 'MASTER', 'HOT', 'DELAYED') THEN 100 "
                "WHEN batch_type = 'BACKFILL' THEN 400 ELSE 500 END)"
            ),
            sa.text(
                "(CASE WHEN batch_type = 'BACKFILL' THEN business_date ELSE NULL END) "
                "DESC NULLS LAST"
            ),
            "scheduled_at",
            "batch_id",
        ],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_index(
        "idx_processing_active_recovery",
        "processing_task",
        ["output_dataset", "business_date"],
        unique=False,
        postgresql_include=["source_batch_id", "queued_at", "started_at"],
        postgresql_where=sa.text(
            "status IN ('WAITING_DEPENDENCY', 'QUEUED', 'RUNNING', "
            "'RETRY_WAIT', 'BLOCKED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_processing_active_recovery",
        table_name="processing_task",
    )
    op.drop_index(
        "idx_collection_batch_active_claim",
        table_name="collection_batch",
    )

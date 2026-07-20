"""add run record recovery lookup index

Revision ID: 20260720_0010
Revises: 20260720_0009
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0010"
down_revision: str | None = "20260720_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_collection_task_recovery",
        "collection_task",
        ["api_name", "scope_key", "finished_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('SUCCESS', 'EMPTY_VALID')"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_collection_task_recovery",
        table_name="collection_task",
    )

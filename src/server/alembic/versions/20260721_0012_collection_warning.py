"""add collection data gap warnings

Revision ID: 20260721_0012
Revises: 20260720_0011
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0012"
down_revision: str | None = "20260720_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_task",
        sa.Column("warning_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("collection_task", "warning_message")

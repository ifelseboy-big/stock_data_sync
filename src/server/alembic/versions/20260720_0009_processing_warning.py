"""add processing data quality warnings

Revision ID: 20260720_0009
Revises: 20260720_0008
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0009"
down_revision: str | None = "20260720_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_task",
        sa.Column("warning_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processing_task", "warning_message")

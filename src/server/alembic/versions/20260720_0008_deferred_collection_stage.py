"""add durable deferred collection stages

Revision ID: 20260720_0008
Revises: 20260719_0007
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0008"
down_revision: str | None = "20260719_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deferred_collection_stage",
        sa.Column("stage_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("batch_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PLANNED')",
            name=op.f("ck_deferred_collection_stage_deferred_collection_stage_status"),
        ),
        sa.CheckConstraint(
            "batch_type IN ('BACKFILL', 'REPAIR')",
            name=op.f("ck_deferred_collection_stage_deferred_collection_stage_batch_type"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["collection_batch.batch_id"],
            name="fk_deferred_collection_stage_batch_id_collection_batch",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["command_id"],
            ["operation_command.command_id"],
            name="fk_deferred_collection_stage_command_id_operation_command",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("stage_id", name="pk_deferred_collection_stage"),
        sa.UniqueConstraint(
            "command_id",
            "api_name",
            "business_date",
            name="uq_deferred_collection_stage_command_api_date",
        ),
    )
    op.create_index(
        "idx_deferred_collection_stage_pending",
        "deferred_collection_stage",
        ["created_at", "stage_id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_deferred_collection_stage_pending",
        table_name="deferred_collection_stage",
    )
    op.drop_table("deferred_collection_stage")

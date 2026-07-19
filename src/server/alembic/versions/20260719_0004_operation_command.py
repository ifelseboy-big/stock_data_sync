"""Add persistent idempotent operations commands.

Revision ID: 20260719_0004
Revises: 20260719_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260719_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_command",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED')",
            name="ck_operation_command_operation_command_status",
        ),
        sa.PrimaryKeyConstraint("command_id", name="pk_operation_command"),
    )
    op.create_index(
        "uq_operation_command_idempotency",
        "operation_command",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "idx_operation_command_created",
        "operation_command",
        ["created_at", "command_id"],
        unique=False,
    )
    op.create_index(
        "idx_operation_command_target",
        "operation_command",
        ["target_type", "target_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_operation_command_target", table_name="operation_command")
    op.drop_index("idx_operation_command_created", table_name="operation_command")
    op.drop_index("uq_operation_command_idempotency", table_name="operation_command")
    op.drop_table("operation_command")

"""Add independent THS theme index datasets.

Revision ID: 20260719_0005
Revises: 20260719_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260719_0005"
down_revision: str | None = "20260719_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "theme_index",
        sa.Column("source", sa.String(length=8), server_default="THS", nullable=False),
        sa.Column("ts_code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("exchange", sa.String(length=8), nullable=True),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("theme_type", sa.String(length=8), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source = 'THS'", name="ck_theme_index_source"),
        sa.CheckConstraint("theme_type = 'TH'", name="ck_theme_index_theme_type"),
        sa.PrimaryKeyConstraint("source", "ts_code", name="pk_theme_index"),
    )
    op.create_table(
        "theme_index_daily",
        sa.Column("source", sa.String(length=8), server_default="THS", nullable=False),
        sa.Column("ts_code", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("pre_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("avg_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("change", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("pct_change", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("volume", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("total_mv", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("float_mv", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "ts_code"],
            ["theme_index.source", "theme_index.ts_code"],
            name="fk_theme_index_daily_index",
        ),
        sa.PrimaryKeyConstraint("source", "ts_code", "trade_date", name="pk_theme_index_daily"),
    )
    op.create_index(
        "idx_theme_index_daily_trade",
        "theme_index_daily",
        ["trade_date", "source", "ts_code"],
        unique=False,
    )
    op.create_table(
        "theme_index_member",
        sa.Column("source", sa.String(length=8), server_default="THS", nullable=False),
        sa.Column("ts_code", sa.String(length=20), nullable=False),
        sa.Column("con_code", sa.String(length=16), nullable=False),
        sa.Column("con_name", sa.String(length=64), nullable=True),
        sa.Column("weight", sa.Numeric(precision=14, scale=8), nullable=True),
        sa.Column("in_date", sa.Date(), nullable=True),
        sa.Column("out_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.Date(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "ts_code"],
            ["theme_index.source", "theme_index.ts_code"],
            name="fk_theme_index_member_index",
        ),
        sa.ForeignKeyConstraint(
            ["con_code"],
            ["stock.ts_code"],
            name="fk_theme_index_member_stock",
        ),
        sa.PrimaryKeyConstraint("source", "ts_code", "con_code", name="pk_theme_index_member"),
    )
    op.create_index(
        "idx_theme_index_member_stock",
        "theme_index_member",
        ["con_code", "source", "ts_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_theme_index_member_stock", table_name="theme_index_member")
    op.drop_table("theme_index_member")
    op.drop_index("idx_theme_index_daily_trade", table_name="theme_index_daily")
    op.drop_table("theme_index_daily")
    op.drop_table("theme_index")

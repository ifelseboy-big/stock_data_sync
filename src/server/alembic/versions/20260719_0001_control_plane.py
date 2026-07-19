"""create control-plane runtime tables

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260719_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collection_batch",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("batch_type", sa.String(length=20), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_version", sa.String(length=64), nullable=True),
        sa.Column("expected_task_count", sa.Integer(), nullable=True),
        sa.Column("planning_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "batch_type IN ('MASTER', 'DAILY', 'HOT', 'DELAYED', 'BACKFILL', 'REPAIR')",
            name="ck_collection_batch_collection_batch_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'CLOSED', 'CANCELLED')",
            name="ck_collection_batch_collection_batch_status",
        ),
        sa.PrimaryKeyConstraint("batch_id", name="pk_collection_batch"),
    )
    op.create_index(
        "uq_collection_batch_slot",
        "collection_batch",
        ["batch_type", "business_date", "scheduled_at"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "idx_batch_active_schedule",
        "collection_batch",
        ["scheduled_at", "batch_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    op.create_table(
        "collection_task",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16), server_default="TUSHARE", nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=256), nullable=False),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'EMPTY_VALID', 'RETRY_WAIT', "
            "'FAILED', 'SKIPPED', 'CANCELLED')",
            name="ck_collection_task_collection_task_status",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["collection_batch.batch_id"],
            name="fk_collection_task_batch_id_collection_batch",
        ),
        sa.PrimaryKeyConstraint("task_id", name="pk_collection_task"),
        sa.UniqueConstraint(
            "batch_id",
            "api_name",
            "scope_key",
            name="uq_collection_task_batch_api_scope",
        ),
    )
    op.create_index(
        "idx_task_batch_status",
        "collection_task",
        ["batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_task_retry_due",
        "collection_task",
        ["next_retry_at", "task_id"],
        unique=False,
        postgresql_where=sa.text("status = 'RETRY_WAIT'"),
    )

    op.create_table(
        "raw_data_asset",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["collection_task.task_id"],
            name="fk_raw_data_asset_task_id_collection_task",
        ),
        sa.PrimaryKeyConstraint("asset_id", name="pk_raw_data_asset"),
        sa.UniqueConstraint("task_id", name="uq_raw_data_asset_task_id"),
    )
    op.create_index(
        "idx_raw_asset_api_date",
        "raw_data_asset",
        ["api_name", "business_date", "fetched_at"],
        unique=False,
    )

    op.create_table(
        "processing_task",
        sa.Column("process_id", sa.Uuid(), nullable=False),
        sa.Column("source_batch_id", sa.Uuid(), nullable=False),
        sa.Column("process_type", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("output_dataset", sa.String(length=64), nullable=False),
        sa.Column("output_version", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="WAITING_DEPENDENCY", nullable=False
        ),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=True),
        sa.Column("rows_rejected", sa.Integer(), nullable=True),
        sa.Column("rows_written", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('WAITING_DEPENDENCY', 'QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCESS', "
            "'BLOCKED', 'FAILED', 'SKIPPED', 'CANCELLED')",
            name="ck_processing_task_processing_task_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_batch_id"],
            ["collection_batch.batch_id"],
            name="fk_processing_task_source_batch_id_collection_batch",
        ),
        sa.PrimaryKeyConstraint("process_id", name="pk_processing_task"),
    )
    op.create_index(
        "uq_processing_output_version",
        "processing_task",
        ["output_version"],
        unique=True,
    )
    op.create_index(
        "idx_process_batch_status",
        "processing_task",
        ["source_batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_process_queue",
        "processing_task",
        ["priority", "queued_at", "process_id"],
        unique=False,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index(
        "idx_processing_retry_due",
        "processing_task",
        ["next_retry_at", "priority", "process_id"],
        unique=False,
        postgresql_where=sa.text("status = 'RETRY_WAIT'"),
    )

    op.create_table(
        "processing_dependency",
        sa.Column("process_id", sa.Uuid(), nullable=False),
        sa.Column("dependency_type", sa.String(length=20), nullable=False),
        sa.Column("dependency_name", sa.String(length=64), nullable=False),
        sa.Column("dependency_scope_key", sa.String(length=256), nullable=False),
        sa.Column("dependency_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_asset_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_release_process_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="WAITING", nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(dependency_type = 'RAW_ASSET' AND resolved_release_process_id IS NULL) OR "
            "(dependency_type = 'DATASET_RELEASE' AND resolved_asset_id IS NULL)",
            name="ck_processing_dependency_dependency_target",
        ),
        sa.CheckConstraint(
            "dependency_type IN ('RAW_ASSET', 'DATASET_RELEASE')",
            name="ck_processing_dependency_processing_dependency_type",
        ),
        sa.CheckConstraint(
            "status IN ('WAITING', 'READY', 'MISSING', 'FAILED')",
            name="ck_processing_dependency_processing_dependency_status",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processing_task.process_id"],
            name="fk_proc_dep_process",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_asset_id"],
            ["raw_data_asset.asset_id"],
            name="fk_proc_dep_asset",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_release_process_id"],
            ["processing_task.process_id"],
            name="fk_proc_dep_release_process",
        ),
        sa.PrimaryKeyConstraint(
            "process_id",
            "dependency_type",
            "dependency_name",
            "dependency_scope_key",
            name="pk_processing_dependency",
        ),
    )
    op.create_index(
        "idx_dependency_asset",
        "processing_dependency",
        ["resolved_asset_id"],
        unique=False,
        postgresql_where=sa.text("resolved_asset_id IS NOT NULL"),
    )
    op.create_index(
        "idx_dependency_release_process",
        "processing_dependency",
        ["resolved_release_process_id"],
        unique=False,
        postgresql_where=sa.text("resolved_release_process_id IS NOT NULL"),
    )
    op.create_index(
        "idx_dependency_waiting",
        "processing_dependency",
        ["process_id", "dependency_type", "status"],
        unique=False,
    )

    op.create_table(
        "dataset_release",
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=256), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("process_id", sa.Uuid(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('GLOBAL', 'DATE', 'MONTH', 'ENTITY')",
            name="ck_dataset_release_dataset_release_scope_type",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processing_task.process_id"],
            name="fk_dataset_release_process_id_processing_task",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_name", "scope_type", "scope_key", name="pk_dataset_release"
        ),
    )
    op.create_index(
        "idx_release_process",
        "dataset_release",
        ["process_id"],
        unique=False,
    )
    op.create_index(
        "idx_release_business_date",
        "dataset_release",
        ["dataset_name", "business_date"],
        unique=False,
        postgresql_where=sa.text("business_date IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_release_business_date", table_name="dataset_release")
    op.drop_index("idx_release_process", table_name="dataset_release")
    op.drop_table("dataset_release")

    op.drop_index("idx_dependency_waiting", table_name="processing_dependency")
    op.drop_index("idx_dependency_release_process", table_name="processing_dependency")
    op.drop_index("idx_dependency_asset", table_name="processing_dependency")
    op.drop_table("processing_dependency")

    op.drop_index("idx_processing_retry_due", table_name="processing_task")
    op.drop_index("idx_process_queue", table_name="processing_task")
    op.drop_index("idx_process_batch_status", table_name="processing_task")
    op.drop_index("uq_processing_output_version", table_name="processing_task")
    op.drop_table("processing_task")

    op.drop_index("idx_raw_asset_api_date", table_name="raw_data_asset")
    op.drop_table("raw_data_asset")

    op.drop_index("idx_task_retry_due", table_name="collection_task")
    op.drop_index("idx_task_batch_status", table_name="collection_task")
    op.drop_table("collection_task")

    op.drop_index("idx_batch_active_schedule", table_name="collection_batch")
    op.drop_index("uq_collection_batch_slot", table_name="collection_batch")
    op.drop_table("collection_batch")

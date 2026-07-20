from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BatchType(StrEnum):
    MASTER = "MASTER"
    DAILY = "DAILY"
    HOT = "HOT"
    DELAYED = "DELAYED"
    BACKFILL = "BACKFILL"
    REPAIR = "REPAIR"


class BatchStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class CollectionTaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    EMPTY_VALID = "EMPTY_VALID"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class CollectionBatch(Base):
    __tablename__ = "collection_batch"
    __table_args__ = (
        CheckConstraint(
            "batch_type IN ('MASTER', 'DAILY', 'HOT', 'DELAYED', 'BACKFILL', 'REPAIR')",
            name="collection_batch_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'CLOSED', 'CANCELLED')",
            name="collection_batch_status",
        ),
        Index(
            "uq_collection_batch_slot",
            "batch_type",
            "business_date",
            "scheduled_at",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "idx_batch_active_schedule",
            "scheduled_at",
            "batch_id",
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_type: Mapped[str] = mapped_column(String(20), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=BatchStatus.PENDING.value,
        server_default=BatchStatus.PENDING.value,
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_task_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planning_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class CollectionTask(Base):
    __tablename__ = "collection_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'EMPTY_VALID', 'RETRY_WAIT', "
            "'FAILED', 'SKIPPED', 'CANCELLED')",
            name="collection_task_status",
        ),
        UniqueConstraint(
            "batch_id",
            "api_name",
            "scope_key",
            name="uq_collection_task_batch_api_scope",
        ),
        Index("idx_task_batch_status", "batch_id", "status"),
        Index(
            "idx_task_retry_due",
            "next_retry_at",
            "task_id",
            postgresql_where=text("status = 'RETRY_WAIT'"),
        ),
        Index(
            "idx_collection_task_recovery",
            "api_name",
            "scope_key",
            "finished_at",
            postgresql_where=text("status IN ('SUCCESS', 'EMPTY_VALID')"),
        ),
    )

    task_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("collection_batch.batch_id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="TUSHARE", server_default="TUSHARE"
    )
    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CollectionTaskStatus.PENDING.value,
        server_default=CollectionTaskStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RawDataAsset(Base):
    __tablename__ = "raw_data_asset"
    __table_args__ = (Index("idx_raw_asset_api_date", "api_name", "business_date", "fetched_at"),)

    asset_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("collection_task.task_id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    request_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

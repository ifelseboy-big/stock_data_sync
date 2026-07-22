from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessingTaskStatus(StrEnum):
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class DependencyType(StrEnum):
    RAW_ASSET = "RAW_ASSET"
    DATASET_RELEASE = "DATASET_RELEASE"


class DependencyStatus(StrEnum):
    WAITING = "WAITING"
    READY = "READY"
    MISSING = "MISSING"
    FAILED = "FAILED"


class ReleaseScopeType(StrEnum):
    GLOBAL = "GLOBAL"
    DATE = "DATE"
    MONTH = "MONTH"
    ENTITY = "ENTITY"


class ProcessingTask(Base):
    __tablename__ = "processing_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('WAITING_DEPENDENCY', 'QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCESS', "
            "'BLOCKED', 'FAILED', 'SKIPPED', 'CANCELLED')",
            name="processing_task_status",
        ),
        Index("uq_processing_output_version", "output_version", unique=True),
        Index("idx_process_batch_status", "source_batch_id", "status"),
        Index(
            "idx_process_queue",
            "priority",
            "queued_at",
            "process_id",
            postgresql_where=text("status = 'QUEUED'"),
        ),
        Index(
            "idx_processing_retry_due",
            "next_retry_at",
            "priority",
            "process_id",
            postgresql_where=text("status = 'RETRY_WAIT'"),
        ),
        Index(
            "idx_processing_active_recovery",
            "output_dataset",
            "business_date",
            postgresql_include=("source_batch_id", "queued_at", "started_at"),
            postgresql_where=text(
                "status IN ('WAITING_DEPENDENCY', 'QUEUED', 'RUNNING', "
                "'RETRY_WAIT', 'BLOCKED')"
            ),
        ),
    )

    process_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("collection_batch.batch_id"), nullable=False
    )
    process_type: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    output_dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    output_version: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
        server_default=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_written: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessingDependency(Base):
    __tablename__ = "processing_dependency"
    __table_args__ = (
        CheckConstraint(
            "(dependency_type = 'RAW_ASSET' AND resolved_release_process_id IS NULL) "
            "OR (dependency_type = 'DATASET_RELEASE' AND resolved_asset_id IS NULL)",
            name="dependency_target",
        ),
        CheckConstraint(
            "dependency_type IN ('RAW_ASSET', 'DATASET_RELEASE')",
            name="processing_dependency_type",
        ),
        CheckConstraint(
            "status IN ('WAITING', 'READY', 'MISSING', 'FAILED')",
            name="processing_dependency_status",
        ),
        Index(
            "idx_dependency_asset",
            "resolved_asset_id",
            postgresql_where=text("resolved_asset_id IS NOT NULL"),
        ),
        Index(
            "idx_dependency_release_process",
            "resolved_release_process_id",
            postgresql_where=text("resolved_release_process_id IS NOT NULL"),
        ),
        Index("idx_dependency_waiting", "process_id", "dependency_type", "status"),
    )

    process_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("processing_task.process_id", name="fk_proc_dep_process"),
        primary_key=True,
    )
    dependency_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    dependency_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    dependency_scope_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    dependency_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("raw_data_asset.asset_id", name="fk_proc_dep_asset"),
        nullable=True,
    )
    resolved_release_process_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("processing_task.process_id", name="fk_proc_dep_release_process"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DependencyStatus.WAITING.value,
        server_default=DependencyStatus.WAITING.value,
    )
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DatasetRelease(Base):
    __tablename__ = "dataset_release"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('GLOBAL', 'DATE', 'MONTH', 'ENTITY')",
            name="dataset_release_scope_type",
        ),
        Index("idx_release_process", "process_id"),
        Index(
            "idx_release_business_date",
            "dataset_name",
            "business_date",
            postgresql_where=text("business_date IS NOT NULL"),
        ),
    )

    dataset_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    process_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("processing_task.process_id"), nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""SQLAlchemy entities for operations observability and manual commands."""

from datetime import date, datetime
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
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderRequestLog(Base):
    __tablename__ = "provider_request_log"
    __table_args__ = (
        CheckConstraint("status IN ('SUCCESS', 'ERROR')", name="provider_request_status"),
        CheckConstraint("duration_ms >= 0", name="provider_request_duration"),
        CheckConstraint("rate_limit_wait_ms >= 0", name="provider_request_wait"),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="provider_request_rows"),
        Index("idx_provider_request_endpoint_time", "provider", "endpoint", "requested_at"),
        Index("idx_provider_request_status_time", "status", "requested_at"),
        Index("idx_provider_request_task", "task_id", "requested_at"),
    )

    request_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("collection_task.task_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_wait_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OperationCommand(Base):
    __tablename__ = "operation_command"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED')",
            name="operation_command_status",
        ),
        Index("uq_operation_command_idempotency", "idempotency_key", unique=True),
        Index("idx_operation_command_created", "created_at", "command_id"),
        Index("idx_operation_command_target", "target_type", "target_id", "created_at"),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeferredCollectionStage(Base):
    __tablename__ = "deferred_collection_stage"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PLANNED')",
            name="deferred_collection_stage_status",
        ),
        CheckConstraint(
            "batch_type IN ('BACKFILL', 'REPAIR')",
            name="deferred_collection_stage_batch_type",
        ),
        UniqueConstraint(
            "command_id",
            "api_name",
            "business_date",
            name="uq_deferred_collection_stage_command_api_date",
        ),
        Index(
            "idx_deferred_collection_stage_pending",
            "created_at",
            "stage_id",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    stage_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    command_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("operation_command.command_id", ondelete="CASCADE"),
        nullable=False,
    )
    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    batch_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("collection_batch.batch_id", ondelete="SET NULL"),
        nullable=True,
    )
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ScheduledJobControl(Base):
    __tablename__ = "scheduled_job_control"

    job_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ScheduledJobExecution(Base):
    __tablename__ = "scheduled_job_execution"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL', 'STARTUP_CATCHUP')",
            name="scheduled_job_execution_trigger",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')",
            name="scheduled_job_execution_status",
        ),
        Index("idx_scheduled_job_execution_created", "created_at"),
        Index("idx_scheduled_job_execution_job_time", "job_id", "created_at"),
        Index(
            "idx_scheduled_job_execution_pending",
            "created_at",
            "execution_id",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    execution_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[str] = mapped_column(String(96), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

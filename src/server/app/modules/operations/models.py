"""SQLAlchemy entities for operations observability and manual commands."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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

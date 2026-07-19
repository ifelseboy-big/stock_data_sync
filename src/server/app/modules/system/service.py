import os
import resource
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.modules.acquisition.capacity import RawStorageCapacityGate
from app.modules.processing.models import ProcessingTask, ProcessingTaskStatus
from app.modules.system.schemas import (
    DatabaseResources,
    ProcessResources,
    SchedulerResources,
    StorageResources,
    SystemResources,
)


async def is_database_ready(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def get_system_resources(
    db: AsyncSession,
    config: Settings = settings,
) -> SystemResources:
    now = datetime.now(ZoneInfo(config.scheduler_timezone))
    version = await db.scalar(select(func.version()))
    database_size = await db.scalar(select(func.pg_database_size(func.current_database())))
    active_connections = await db.scalar(select(func.count()).select_from(text("pg_stat_activity")))
    long_transactions = await db.scalar(
        select(func.count())
        .select_from(text("pg_stat_activity"))
        .where(text("xact_start IS NOT NULL AND xact_start < :cutoff"))
        .params(cutoff=now - timedelta(minutes=5))
    )
    scheduler_lock_held = bool(
        await db.scalar(
            select(func.count())
            .select_from(text("pg_locks"))
            .where(text("locktype = 'advisory' AND classid = 0 AND objid = :lock_id"))
            .params(lock_id=config.scheduler_advisory_lock_id)
        )
    )
    processing_running = await db.scalar(
        select(func.count())
        .select_from(ProcessingTask)
        .where(ProcessingTask.status == ProcessingTaskStatus.RUNNING.value)
    )
    capacity = RawStorageCapacityGate(config.raw_data_dir, config).snapshot()
    load_average = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    memory_high_water = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        memory_high_water *= 1024
    return SystemResources(
        generated_at=now,
        database=DatabaseResources(
            status="ok",
            version=str(version or "unknown"),
            size_bytes=int(database_size or 0),
            active_connection_count=int(active_connections or 0),
            long_transaction_count=int(long_transactions or 0),
        ),
        scheduler=SchedulerResources(
            status="running" if scheduler_lock_held else "stopped",
            singleton_lock_held=scheduler_lock_held,
            processing_running_count=int(processing_running or 0),
        ),
        storage=StorageResources(
            level=capacity.level.value,
            path=str(config.raw_data_dir),
            total_bytes=capacity.total_bytes,
            used_bytes=capacity.used_bytes,
            free_bytes=capacity.free_bytes,
            used_percent=capacity.used_percent,
        ),
        process=ProcessResources(
            app_version=config.app_version,
            cpu_count=os.cpu_count() or 1,
            load_average_one_minute=load_average,
            memory_high_water_bytes=memory_high_water,
        ),
    )

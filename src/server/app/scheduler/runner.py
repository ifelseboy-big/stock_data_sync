from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.modules.acquisition.factory import shutdown_acquisition_runtime
from app.modules.processing.factory import shutdown_processing_runtime
from app.scheduler.factory import create_scheduler
from app.scheduler.jobs import (
    plan_due_collection_stages,
    reconcile_collection_runtime,
    reconcile_processing_runtime,
)
from app.scheduler.lock import scheduler_singleton_lock
from app.scheduler.management import (
    ensure_scheduled_job_controls,
    execute_scheduled_job,
    recover_interrupted_scheduled_job_executions,
)


def _run_startup_job(job_id: str) -> None:
    try:
        execute_scheduled_job(job_id, "STARTUP_CATCHUP")
    except Exception:
        structlog.get_logger("scheduler").exception(
            "startup_catchup_failed",
            job_id=job_id,
        )


def main() -> None:
    configure_logging()
    logger = structlog.get_logger("scheduler")
    scheduler = create_scheduler()
    logger.info(
        "scheduler_starting",
        timezone=settings.scheduler_timezone,
        jobstore="postgresql",
    )
    with scheduler_singleton_lock():
        try:
            ensure_scheduled_job_controls()
            recovered_execution_count = recover_interrupted_scheduled_job_executions()
            if recovered_execution_count:
                logger.warning(
                    "interrupted_scheduled_jobs_recovered",
                    execution_count=recovered_execution_count,
                )
            execute_scheduled_job("ensure-future-partitions", "STARTUP_CATCHUP")
            reconcile_collection_runtime(
                recover_all_running=True,
                audit_all_assets=False,
            )
            reconcile_processing_runtime(recover_all_running=True)
            execute_scheduled_job("close-collection-batches", "STARTUP_CATCHUP")
            execute_scheduled_job("plan-processing-tasks", "STARTUP_CATCHUP")
            for job_id in (
                "plan-trade-calendar",
                "plan-stock-master",
                "plan-etf-master",
                "plan-special-master",
                "plan-concept-board-members",
                "plan-monthly-index-weights",
            ):
                _run_startup_job(job_id)
            if datetime.now(ZoneInfo(settings.scheduler_timezone)).month >= 10:
                _run_startup_job("plan-next-year-trade-calendar")
            try:
                plan_due_collection_stages()
            except Exception:
                logger.exception("daily_startup_catchup_failed")
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("scheduler_stopped")
        finally:
            shutdown_processing_runtime()
            shutdown_acquisition_runtime()


if __name__ == "__main__":
    main()

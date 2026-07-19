import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.modules.acquisition.factory import shutdown_acquisition_runtime
from app.scheduler.factory import create_scheduler
from app.scheduler.jobs import (
    close_collection_batches,
    ensure_future_partitions,
    plan_due_collection_stages,
    plan_etf_master,
    plan_processing_tasks,
    plan_trade_calendar,
    reconcile_collection_runtime,
    reconcile_processing_runtime,
)
from app.scheduler.lock import scheduler_singleton_lock


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
            ensure_future_partitions()
            reconcile_collection_runtime(recover_all_running=True)
            reconcile_processing_runtime(recover_all_running=True)
            close_collection_batches()
            plan_processing_tasks()
            plan_trade_calendar()
            plan_etf_master()
            plan_due_collection_stages()
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("scheduler_stopped")
        finally:
            shutdown_acquisition_runtime()


if __name__ == "__main__":
    main()

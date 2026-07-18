import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.scheduler.factory import create_scheduler
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
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from app.core.config import settings


def configure_logging() -> None:
    handlers: list[logging.Handler] = []
    if settings.app_log_file is None:
        handlers.append(logging.StreamHandler())
    else:
        log_file = Path(settings.app_log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=settings.app_log_max_bytes,
                backupCount=settings.app_log_backup_count,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )

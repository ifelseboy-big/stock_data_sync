import json
import logging
from pathlib import Path

import structlog

from app.core.config import settings
from app.core.logging import configure_logging


def test_structlog_writes_json_to_configured_log_file(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    log_file = tmp_path / "server.log"
    original_log_file = settings.app_log_file
    monkeypatch.setattr(settings, "app_log_file", log_file)  # type: ignore[attr-defined]

    try:
        configure_logging()
        logger = structlog.get_logger("scheduler")
        logger.info("job_completed", job_id="daily-sync")
        try:
            raise RuntimeError("provider unavailable")
        except RuntimeError:
            logger.exception("job_failed", job_id="daily-sync")

        for handler in logging.getLogger().handlers:
            handler.flush()

        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        assert records[0]["event"] == "job_completed"
        assert records[0]["logger"] == "scheduler"
        assert records[0]["level"] == "info"
        assert records[0]["job_id"] == "daily-sync"
        assert records[1]["event"] == "job_failed"
        assert records[1]["level"] == "error"
        assert "RuntimeError: provider unavailable" in records[1]["exception"]
    finally:
        monkeypatch.setattr(  # type: ignore[attr-defined]
            settings,
            "app_log_file",
            original_log_file,
        )
        configure_logging()

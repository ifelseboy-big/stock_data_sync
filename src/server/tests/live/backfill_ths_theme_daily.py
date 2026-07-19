from __future__ import annotations

import argparse
import json
from datetime import date
from uuid import uuid4

from sqlalchemy import func, select

from app.db.sync_session import SyncSessionFactory
from app.modules.processing.models import DatasetRelease
from app.modules.topics.models import ThemeIndexDaily
from tests.live.verify_recent_workflows import (
    OperationsApi,
    _assert_collection_success,
    _batch_ids_from_command,
    _close_batches,
    _drain_collection,
    _plan_and_drain_processing,
    _trading_dates,
    _validate_environment,
)


def run(start_date: date, end_date: date) -> dict[str, object]:
    _validate_environment()
    trading_dates = _trading_dates(start_date, end_date)
    api = OperationsApi()
    try:
        result = api.post(
            "/api/v1/operations/commands/backfills",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "apiNames": ["ths_daily"],
                "reason": "补齐同花顺题材指数历史日线",
            },
            f"live-ths-daily-backfill-{start_date:%Y%m%d}-{end_date:%Y%m%d}-{uuid4()}",
        )
        batch_ids = _batch_ids_from_command(result)
        _drain_collection()
        _close_batches()
        _assert_collection_success(batch_ids, "THS theme daily backfill")
        _plan_and_drain_processing(batch_ids, "THS theme daily backfill")
    finally:
        api.close()

    counts: dict[str, int] = {}
    with SyncSessionFactory() as session:
        for business_date in trading_dates:
            daily_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(ThemeIndexDaily)
                    .where(ThemeIndexDaily.trade_date == business_date)
                )
                or 0
            )
            release_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(DatasetRelease)
                    .where(
                        DatasetRelease.dataset_name == "theme_index_daily",
                        DatasetRelease.scope_type == "DATE",
                        DatasetRelease.scope_key == business_date.isoformat(),
                    )
                )
                or 0
            )
            if daily_count == 0 or release_count == 0:
                raise RuntimeError(
                    f"theme_index_daily backfill incomplete for {business_date}: "
                    f"rows={daily_count}, releases={release_count}"
                )
            counts[business_date.isoformat()] = daily_count

    return {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "batchIds": [str(item) for item in batch_ids],
        "dailyRows": counts,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回补并验证同花顺题材指数日线")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.start, arguments.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

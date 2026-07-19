import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select

from app.catalog import WriteStrategy
from app.db.sync_session import SyncSessionFactory
from app.modules.processing.staging import PostgresStagingPublisher
from app.modules.stocks.models import TradeCalendar

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


def test_staging_publisher_copies_and_merges_rows() -> None:
    business_date = date(2099, 12, 31)
    published_at = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        session.execute(
            delete(TradeCalendar).where(
                TradeCalendar.exchange == "PERF",
                TradeCalendar.cal_date == business_date,
            )
        )
        written = PostgresStagingPublisher().publish(
            session,
            target=TradeCalendar.__table__,
            rows=(
                {
                    "exchange": "PERF",
                    "cal_date": business_date,
                    "is_open": True,
                    "pretrade_date": None,
                    "synced_at": published_at,
                },
            ),
            strategy=WriteStrategy.UPSERT_KEY,
            key_columns=("exchange", "cal_date"),
            update_columns=("is_open", "pretrade_date", "synced_at"),
        )
        assert written == 1
        saved = session.scalar(
            select(TradeCalendar).where(
                TradeCalendar.exchange == "PERF",
                TradeCalendar.cal_date == business_date,
            )
        )
        assert saved is not None
        assert saved.is_open is True


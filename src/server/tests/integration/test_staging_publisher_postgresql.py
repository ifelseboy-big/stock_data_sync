import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select, text

from app.catalog import WriteStrategy
from app.db.sync_session import SyncSessionFactory
from app.modules.processing.staging import PostgresStagingPublisher
from app.modules.stocks.models import Stock, StockTechnicalDaily, TradeCalendar

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


def test_staging_publisher_creates_missing_target_partition_before_write() -> None:
    business_date = date(2099, 12, 31)
    partition_name = "stock_technical_daily_p209912"
    published_at = datetime.now(UTC)
    ts_code = "T999999.TEST"

    with SyncSessionFactory() as session:
        session.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
        session.execute(delete(Stock).where(Stock.ts_code == ts_code))
        session.add(
            Stock(
                ts_code=ts_code,
                symbol="T999999",
                name="partition write test",
                exchange="TEST",
                list_status="L",
                synced_at=published_at,
            )
        )
        session.flush()

        rows = (
            {
                "ts_code": ts_code,
                "trade_date": business_date,
                "synced_at": published_at,
            },
        )
        written = PostgresStagingPublisher().publish(
            session,
            target=StockTechnicalDaily.__table__,
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("ts_code", "trade_date"),
            update_columns=("synced_at",),
            replace_filters={"trade_date": business_date},
        )
        rewritten = PostgresStagingPublisher().publish(
            session,
            target=StockTechnicalDaily.__table__,
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("ts_code", "trade_date"),
            update_columns=("synced_at",),
            replace_filters={"trade_date": business_date},
        )

        assert written == 1
        assert rewritten == 1
        assert session.scalar(select(func.to_regclass(partition_name))) == partition_name
        assert session.get(StockTechnicalDaily, (ts_code, business_date)) is not None
        session.rollback()

import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.db.sync_session import SyncSessionFactory
from app.modules.processing.processors.base import PreparedDataset
from app.modules.processing.processors.board_moneyflow import (
    BoardMoneyflowRows,
    ThsBoardMoneyflowDailyProcessor,
)
from app.modules.processing.processors.topics import DatedRows, MarketThemeMemberDailyProcessor
from app.modules.stocks.models import Stock, ThsBoardMoneyflowDaily
from app.modules.topics.models import MarketThemeMemberDaily

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


def test_provider_compatibility_rows_publish_with_latest_schema() -> None:
    business_date = date(2099, 12, 30)
    published_at = datetime.now(UTC)
    stock_code = "T999998.TEST"
    with SyncSessionFactory() as session, session.begin():
        session.add(
            Stock(
                ts_code=stock_code,
                symbol="T999998",
                name="provider compatibility test",
                exchange="TEST",
                list_status="L",
                synced_at=published_at,
            )
        )
        session.flush()

        board_result = ThsBoardMoneyflowDailyProcessor().write(
            session,
            PreparedDataset(
                BoardMoneyflowRows(
                    business_date,
                    (
                        {
                            "board_type": "CONCEPT",
                            "board_name": "AI视频",
                            "trade_date": business_date,
                            "ts_code": None,
                            "lead_stock": "示例股份",
                            "lead_stock_price": None,
                            "pct_change": None,
                            "board_index": None,
                            "company_num": None,
                            "lead_stock_pct_change": None,
                            "net_buy_amount": None,
                            "net_sell_amount": None,
                            "net_amount": None,
                        },
                    ),
                ),
                1,
            ),
            published_at=published_at,
        )
        member_result = MarketThemeMemberDailyProcessor().write(
            session,
            PreparedDataset(
                DatedRows(
                    business_date,
                    (
                        {
                            "source": "DC",
                            "trade_date": business_date,
                            "theme_code": "DCTEST",
                            "ts_code": stock_code,
                            "name": "provider compatibility test",
                            "industry_code": None,
                            "industry": None,
                            "reason": None,
                            "hot_num": None,
                        },
                    ),
                ),
                1,
            ),
            published_at=published_at,
        )

        board = session.scalar(
            select(ThsBoardMoneyflowDaily).where(
                ThsBoardMoneyflowDaily.board_type == "CONCEPT",
                ThsBoardMoneyflowDaily.board_name == "AI视频",
                ThsBoardMoneyflowDaily.trade_date == business_date,
            )
        )
        member = session.get(
            MarketThemeMemberDaily,
            ("DC", business_date, "DCTEST", stock_code),
        )
        assert board_result.rows_written == 1
        assert board is not None and board.ts_code is None
        assert member_result.rows_written == 1
        assert member is not None

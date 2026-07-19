from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.tushare import MONEYFLOW_CNT_THS_SPEC, MONEYFLOW_IND_THS_SPEC
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.processors.raw_reader import RawRow, read_raw_assets
from app.modules.processing.processors.transforms import (
    decimal_value,
    integer_value,
    optional_text,
    require_business_date,
    required_text,
    scaled_decimal,
    yyyymmdd,
)
from app.modules.processing.staging import PostgresStagingPublisher, PreparedRow
from app.modules.stocks.models import ThsBoardMoneyflowDaily
from app.storage import RawAssetStore


@dataclass(frozen=True, slots=True)
class BoardMoneyflowRows:
    business_date: date
    rows: tuple[PreparedRow, ...]


class ThsBoardMoneyflowDailyProcessor:
    name = "ths_board_moneyflow_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        if task.business_date is None:
            raise ProcessingError(f"{self.name} requires a business date")
        raw = read_raw_assets(
            dependencies,
            asset_store,
            (MONEYFLOW_CNT_THS_SPEC, MONEYFLOW_IND_THS_SPEC),
        )
        rows = tuple(
            _concept_flow_row(row, task.business_date)
            for row in raw.rows_by_api["moneyflow_cnt_ths"]
        ) + tuple(
            _industry_flow_row(row, task.business_date)
            for row in raw.rows_by_api["moneyflow_ind_ths"]
        )
        return PreparedDataset(BoardMoneyflowRows(task.business_date, rows), raw.row_count)

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        payload = cast(BoardMoneyflowRows, prepared.payload)
        rows = tuple({**row, "synced_at": published_at} for row in payload.rows)
        written = self._publisher.publish(
            session,
            target=cast(Table, ThsBoardMoneyflowDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("board_type", "ts_code", "trade_date"),
            update_columns=(
                "board_name",
                "lead_stock",
                "lead_stock_price",
                "pct_change",
                "board_index",
                "company_num",
                "lead_stock_pct_change",
                "net_buy_amount",
                "net_sell_amount",
                "net_amount",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written)


def _concept_flow_row(source: RawRow, business_date: date) -> PreparedRow:
    return _base_flow_row(
        source,
        business_date,
        board_type="CONCEPT",
        board_name=required_text(source.get("name"), "name"),
        lead_stock_price=source.get("close_price"),
        board_index=source.get("industry_index"),
    )


def _industry_flow_row(source: RawRow, business_date: date) -> PreparedRow:
    return _base_flow_row(
        source,
        business_date,
        board_type="INDUSTRY",
        board_name=required_text(source.get("industry"), "industry"),
        lead_stock_price=source.get("close"),
        board_index=source.get("close_price"),
    )


def _base_flow_row(
    source: RawRow,
    business_date: date,
    *,
    board_type: str,
    board_name: str,
    lead_stock_price: object,
    board_index: object,
) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "ths_board_moneyflow_daily")
    return {
        "board_type": board_type,
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "board_name": board_name,
        "lead_stock": optional_text(source.get("lead_stock")),
        "lead_stock_price": decimal_value(lead_stock_price, "lead_stock_price"),
        "pct_change": decimal_value(source.get("pct_change"), "pct_change"),
        "board_index": decimal_value(board_index, "board_index"),
        "company_num": integer_value(source.get("company_num"), "company_num"),
        "lead_stock_pct_change": decimal_value(source.get("pct_change_stock"), "pct_change_stock"),
        "net_buy_amount": scaled_decimal(
            source.get("net_buy_amount"), "net_buy_amount", 100_000_000
        ),
        "net_sell_amount": scaled_decimal(
            source.get("net_sell_amount"), "net_sell_amount", 100_000_000
        ),
        "net_amount": scaled_decimal(source.get("net_amount"), "net_amount", 100_000_000),
    }

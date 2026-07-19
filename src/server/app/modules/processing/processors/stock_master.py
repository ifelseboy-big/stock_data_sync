from datetime import datetime
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.tushare import STOCK_BASIC_SPEC, STOCK_COMPANY_SPEC
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.processors.raw_reader import read_raw_assets
from app.modules.processing.processors.transforms import (
    integer_value,
    optional_text,
    optional_yyyymmdd,
    required_text,
    scaled_decimal,
)
from app.modules.processing.staging import PostgresStagingPublisher, PreparedRow
from app.modules.stocks.models import Stock, StockCompany
from app.storage import RawAssetStore

STOCK_KEY = ("ts_code",)
STOCK_UPDATE_COLUMNS = (
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "enname",
    "cnspell",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
    "act_name",
    "act_ent_type",
    "synced_at",
)
COMPANY_UPDATE_COLUMNS = (
    "com_name",
    "com_id",
    "exchange",
    "chairman",
    "manager",
    "secretary",
    "reg_capital",
    "setup_date",
    "province",
    "city",
    "introduction",
    "website",
    "email",
    "office",
    "employees",
    "main_business",
    "business_scope",
    "synced_at",
)


class StockProcessor:
    name = "stock"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (STOCK_BASIC_SPEC,))
        rows = tuple(_stock_row(row) for row in raw.rows_by_api["stock_basic"])
        if not rows:
            raise ProcessingError("stock master dataset cannot be empty")
        return PreparedDataset(payload=rows, rows_read=raw.row_count)

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        rows = cast(tuple[PreparedRow, ...], prepared.payload)
        values = tuple({**row, "synced_at": published_at} for row in rows)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, Stock.__table__),
                rows=values,
                strategy=WriteStrategy.MASTER_MERGE,
                key_columns=STOCK_KEY,
                update_columns=STOCK_UPDATE_COLUMNS,
            )
        )


class StockCompanyProcessor:
    name = "stock_company"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (STOCK_COMPANY_SPEC,))
        rows = tuple(_company_row(row) for row in raw.rows_by_api["stock_company"])
        if not rows:
            raise ProcessingError("stock_company dataset cannot be empty")
        return PreparedDataset(payload=rows, rows_read=raw.row_count)

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        rows = cast(tuple[PreparedRow, ...], prepared.payload)
        codes = {cast(str, row["ts_code"]) for row in rows}
        existing = set(session.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(codes))))
        values = tuple(
            {**row, "synced_at": published_at} for row in rows if row["ts_code"] in existing
        )
        if not values:
            raise ProcessingError("stock_company has no rows matching the stock master")
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockCompany.__table__),
                rows=values,
                strategy=WriteStrategy.MASTER_MERGE,
                key_columns=STOCK_KEY,
                update_columns=COMPANY_UPDATE_COLUMNS,
            ),
            rows_rejected=len(rows) - len(values),
        )


def _stock_row(source: dict[str, object]) -> PreparedRow:
    exchange = required_text(source.get("exchange"), "exchange")
    if exchange not in {"SSE", "SZSE", "BSE"}:
        raise ProcessingError(f"unsupported stock exchange: {exchange}")
    list_status = required_text(source.get("list_status"), "list_status")
    if list_status not in {"L", "D", "P", "G"}:
        raise ProcessingError(f"invalid list_status: {list_status}")
    is_hs = optional_text(source.get("is_hs"))
    if is_hs not in {None, "N", "H", "S"}:
        raise ProcessingError(f"invalid is_hs: {is_hs}")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "symbol": required_text(source.get("symbol"), "symbol"),
        "name": required_text(source.get("name"), "name"),
        "area": optional_text(source.get("area")),
        "industry": optional_text(source.get("industry")),
        "fullname": optional_text(source.get("fullname")),
        "enname": optional_text(source.get("enname")),
        "cnspell": optional_text(source.get("cnspell")),
        "market": optional_text(source.get("market")),
        "exchange": exchange,
        "curr_type": optional_text(source.get("curr_type")),
        "list_status": list_status,
        "list_date": optional_yyyymmdd(source.get("list_date"), "list_date"),
        "delist_date": optional_yyyymmdd(source.get("delist_date"), "delist_date"),
        "is_hs": is_hs,
        "act_name": optional_text(source.get("act_name")),
        "act_ent_type": optional_text(source.get("act_ent_type")),
    }


def _company_row(source: dict[str, object]) -> PreparedRow:
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "com_name": optional_text(source.get("com_name")),
        "com_id": optional_text(source.get("com_id")),
        "exchange": optional_text(source.get("exchange")),
        "chairman": optional_text(source.get("chairman")),
        "manager": optional_text(source.get("manager")),
        "secretary": optional_text(source.get("secretary")),
        "reg_capital": scaled_decimal(source.get("reg_capital"), "reg_capital", 10_000),
        "setup_date": optional_yyyymmdd(source.get("setup_date"), "setup_date"),
        "province": optional_text(source.get("province")),
        "city": optional_text(source.get("city")),
        "introduction": optional_text(source.get("introduction")),
        "website": optional_text(source.get("website")),
        "email": optional_text(source.get("email")),
        "office": optional_text(source.get("office")),
        "employees": integer_value(source.get("employees"), "employees"),
        "main_business": optional_text(source.get("main_business")),
        "business_scope": optional_text(source.get("business_scope")),
    }

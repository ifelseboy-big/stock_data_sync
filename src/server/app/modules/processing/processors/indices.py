from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from sqlalchemy import Table, delete, insert, select
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.tushare import (
    INDEX_BASIC_SPEC,
    INDEX_DAILY_BASIC_SPEC,
    INDEX_DAILY_SPEC,
    INDEX_WEIGHT_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.indices.models import (
    IndexDailyBasic,
    MarketIndex,
    MarketIndexDaily,
    MarketIndexWeight,
)
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.processors.raw_reader import RawRow, read_raw_assets
from app.modules.processing.processors.transforms import (
    decimal_value,
    optional_text,
    optional_yyyymmdd,
    require_business_date,
    required_text,
    scaled_decimal,
    yyyymmdd,
)
from app.modules.processing.staging import PostgresStagingPublisher, PreparedRow
from app.modules.stocks.models import Stock
from app.storage import RawAssetStore


@dataclass(frozen=True, slots=True)
class IndexDatedRows:
    business_date: date
    rows: tuple[PreparedRow, ...]


class MarketIndexProcessor:
    name = "market_index"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (INDEX_BASIC_SPEC,))
        rows = tuple(_market_index_row(row) for row in raw.rows_by_api["index_basic"])
        if not rows:
            raise ProcessingError("market_index cannot be empty")
        return PreparedDataset(rows, raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        source_rows = cast(tuple[PreparedRow, ...], prepared.payload)
        rows = tuple({**row, "synced_at": published_at} for row in source_rows)
        written = self._publisher.publish(
            session,
            target=cast(Table, MarketIndex.__table__),
            rows=rows,
            strategy=WriteStrategy.MASTER_MERGE,
            key_columns=("ts_code",),
            update_columns=(
                "name",
                "fullname",
                "market",
                "publisher",
                "index_type",
                "category",
                "base_date",
                "base_point",
                "list_date",
                "weight_rule",
                "description",
                "exp_date",
                "synced_at",
            ),
        )
        return PublicationResult(written)


class MarketIndexDailyProcessor:
    name = "market_index_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (INDEX_DAILY_SPEC,))
        rows = tuple(
            _market_index_daily_row(row, business_date) for row in raw.rows_by_api["index_daily"]
        )
        return PreparedDataset(IndexDatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_to_index_master(session, prepared)
        if not matched:
            raise ProcessingError("market_index_daily has no rows matching market_index")
        rows = tuple({**row, "synced_at": published_at} for row in matched)
        written = self._publisher.publish(
            session,
            target=cast(Table, MarketIndexDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("ts_code", "trade_date"),
            update_columns=(
                "close",
                "open",
                "high",
                "low",
                "pre_close",
                "change",
                "pct_chg",
                "volume",
                "amount",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class IndexDailyBasicProcessor:
    name = "index_daily_basic"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (INDEX_DAILY_BASIC_SPEC,))
        rows = tuple(
            _index_daily_basic_row(row, business_date)
            for row in raw.rows_by_api["index_dailybasic"]
        )
        return PreparedDataset(IndexDatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_to_index_master(session, prepared)
        rows = tuple({**row, "synced_at": published_at} for row in matched)
        written = self._publisher.publish(
            session,
            target=cast(Table, IndexDailyBasic.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("ts_code", "trade_date"),
            update_columns=(
                "total_mv",
                "float_mv",
                "total_share",
                "float_share",
                "free_share",
                "turnover_rate",
                "turnover_rate_f",
                "pe",
                "pe_ttm",
                "pb",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class MarketIndexWeightProcessor:
    name = "market_index_weight"

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        target_month = _business_date(task, self.name).replace(day=1)
        raw = read_raw_assets(dependencies, asset_store, (INDEX_WEIGHT_SPEC,))
        rows = tuple(
            _index_weight_row(row, target_month) for row in raw.rows_by_api["index_weight"]
        )
        return PreparedDataset(IndexDatedRows(target_month, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(IndexDatedRows, prepared.payload)
        index_codes = {cast(str, row["index_code"]) for row in payload.rows}
        member_codes = {cast(str, row["con_code"]) for row in payload.rows}
        indices = set(
            session.scalars(select(MarketIndex.ts_code).where(MarketIndex.ts_code.in_(index_codes)))
        )
        stocks = set(session.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(member_codes))))
        matched = tuple(
            row
            for row in payload.rows
            if row["index_code"] in indices and row["con_code"] in stocks
        )
        rows = tuple({**row, "synced_at": published_at} for row in matched)
        next_month = _next_month(payload.business_date)
        session.execute(
            delete(MarketIndexWeight).where(
                MarketIndexWeight.snapshot_date >= payload.business_date,
                MarketIndexWeight.snapshot_date < next_month,
                MarketIndexWeight.index_code.in_(index_codes),
            )
        )
        if rows:
            session.execute(insert(MarketIndexWeight), rows)
        return PublicationResult(len(rows), len(payload.rows) - len(matched))


def _market_index_row(source: RawRow) -> PreparedRow:
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": required_text(source.get("name"), "name"),
        "fullname": optional_text(source.get("fullname")),
        "market": required_text(source.get("market"), "market"),
        "publisher": optional_text(source.get("publisher")),
        "index_type": optional_text(source.get("index_type")),
        "category": optional_text(source.get("category")),
        "base_date": optional_yyyymmdd(source.get("base_date"), "base_date"),
        "base_point": decimal_value(source.get("base_point"), "base_point"),
        "list_date": optional_yyyymmdd(source.get("list_date"), "list_date"),
        "weight_rule": optional_text(source.get("weight_rule")),
        "description": optional_text(source.get("desc")),
        "exp_date": optional_yyyymmdd(source.get("exp_date"), "exp_date"),
    }


def _market_index_daily_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "market_index_daily")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "close": decimal_value(source.get("close"), "close", required=True),
        "open": decimal_value(source.get("open"), "open"),
        "high": decimal_value(source.get("high"), "high"),
        "low": decimal_value(source.get("low"), "low"),
        "pre_close": decimal_value(source.get("pre_close"), "pre_close"),
        "change": decimal_value(source.get("change"), "change"),
        "pct_chg": decimal_value(source.get("pct_chg"), "pct_chg"),
        "volume": scaled_decimal(source.get("vol"), "vol", 100),
        "amount": scaled_decimal(source.get("amount"), "amount", 1_000),
    }


def _index_daily_basic_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "index_daily_basic")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "total_mv": decimal_value(source.get("total_mv"), "total_mv"),
        "float_mv": decimal_value(source.get("float_mv"), "float_mv"),
        "total_share": decimal_value(source.get("total_share"), "total_share"),
        "float_share": decimal_value(source.get("float_share"), "float_share"),
        "free_share": decimal_value(source.get("free_share"), "free_share"),
        "turnover_rate": decimal_value(source.get("turnover_rate"), "turnover_rate"),
        "turnover_rate_f": decimal_value(source.get("turnover_rate_f"), "turnover_rate_f"),
        "pe": decimal_value(source.get("pe"), "pe"),
        "pe_ttm": decimal_value(source.get("pe_ttm"), "pe_ttm"),
        "pb": decimal_value(source.get("pb"), "pb"),
    }


def _index_weight_row(source: RawRow, target_month: date) -> PreparedRow:
    snapshot_date = yyyymmdd(source.get("trade_date"), "trade_date")
    if snapshot_date.replace(day=1) != target_month:
        raise ProcessingError(
            f"market_index_weight contains {snapshot_date}, expected month {target_month:%Y-%m}"
        )
    return {
        "index_code": required_text(source.get("index_code"), "index_code"),
        "snapshot_date": snapshot_date,
        "con_code": required_text(source.get("con_code"), "con_code"),
        "weight": decimal_value(source.get("weight"), "weight", required=True),
    }


def _filter_to_index_master(
    session: Session, prepared: PreparedDataset
) -> tuple[IndexDatedRows, tuple[PreparedRow, ...]]:
    payload = cast(IndexDatedRows, prepared.payload)
    codes = {cast(str, row["ts_code"]) for row in payload.rows}
    existing = set(
        session.scalars(select(MarketIndex.ts_code).where(MarketIndex.ts_code.in_(codes)))
    )
    return payload, tuple(row for row in payload.rows if row["ts_code"] in existing)


def _business_date(task: ClaimedProcessingTask, dataset: str) -> date:
    if task.business_date is None:
        raise ProcessingError(f"{dataset} requires a business date")
    return task.business_date


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)

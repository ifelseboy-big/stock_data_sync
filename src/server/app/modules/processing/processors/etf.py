from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.tushare import (
    ETF_BASIC_SPEC,
    ETF_SHARE_SIZE_SPEC,
    FUND_ADJ_SPEC,
    FUND_DAILY_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.etfs.models import Etf, EtfDaily, EtfShareSizeDaily
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
from app.storage import RawAssetStore

ETF_KEY = ("ts_code",)
ETF_DAILY_KEY = ("ts_code", "trade_date")
ETF_UPDATE_COLUMNS = (
    "csname",
    "extname",
    "cname",
    "index_code",
    "index_name",
    "setup_date",
    "list_date",
    "list_status",
    "exchange",
    "source_exchange",
    "mgr_name",
    "custod_name",
    "mgt_fee",
    "etf_type",
    "synced_at",
)
ETF_DAILY_UPDATE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "volume",
    "amount",
    "adj_factor",
    "synced_at",
)
ETF_SHARE_SIZE_UPDATE_COLUMNS = (
    "etf_name",
    "total_share",
    "total_size",
    "nav",
    "close",
    "exchange",
    "synced_at",
)


@dataclass(frozen=True, slots=True)
class EtfShareSizeRows:
    business_date: date
    rows: tuple[PreparedRow, ...]


class EtfProcessor:
    name = "etf"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (ETF_BASIC_SPEC,))
        rows = tuple(_etf_row(row) for row in raw.rows_by_api["etf_basic"])
        if not rows:
            raise ProcessingError("ETF master dataset cannot be empty")
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
                target=cast(Table, Etf.__table__),
                rows=values,
                strategy=WriteStrategy.MASTER_MERGE,
                key_columns=ETF_KEY,
                update_columns=ETF_UPDATE_COLUMNS,
            )
        )


class EtfDailyProcessor:
    name = "etf_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        raw = read_raw_assets(
            dependencies,
            asset_store,
            (FUND_DAILY_SPEC, FUND_ADJ_SPEC),
        )
        daily = _key_by_security_date(
            raw.rows_by_api["fund_daily"],
            task.business_date,
            self.name,
        )
        factors = _key_by_security_date(
            raw.rows_by_api["fund_adj"],
            task.business_date,
            self.name,
        )
        rows = tuple(
            _etf_daily_row(source, factors.get(key), task.business_date)
            for key, source in sorted(daily.items())
        )
        if not rows:
            raise ProcessingError("etf_daily cannot publish an empty trading day")
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=len(set(factors) - set(daily)),
        )

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        rows = cast(tuple[PreparedRow, ...], prepared.payload)
        business_date = _single_business_date(rows, self.name)
        matched, rejected = _filter_to_etf_master(session, rows)
        if not matched:
            raise ProcessingError("etf_daily has no rows matching the ETF master")
        values = tuple({**row, "synced_at": published_at} for row in matched)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, EtfDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=ETF_DAILY_KEY,
                update_columns=ETF_DAILY_UPDATE_COLUMNS,
                replace_filters={"trade_date": business_date},
            ),
            rows_rejected=rejected,
        )


class EtfShareSizeDailyProcessor:
    name = "etf_share_size_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        if task.business_date is None:
            raise ProcessingError("etf_share_size_daily requires a business date")
        raw = read_raw_assets(dependencies, asset_store, (ETF_SHARE_SIZE_SPEC,))
        rows = tuple(
            row
            for source in raw.rows_by_api["etf_share_size"]
            if (row := _etf_share_size_row(source, task.business_date)) is not None
        )
        return PreparedDataset(
            payload=EtfShareSizeRows(task.business_date, rows),
            rows_read=raw.row_count,
            rows_rejected=raw.row_count - len(rows),
        )

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        payload = cast(EtfShareSizeRows, prepared.payload)
        matched, rejected = _filter_to_etf_master(session, payload.rows)
        if payload.rows and not matched:
            raise ProcessingError("etf_share_size_daily has no rows matching the ETF master")
        values = tuple({**row, "synced_at": published_at} for row in matched)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, EtfShareSizeDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=ETF_DAILY_KEY,
                update_columns=ETF_SHARE_SIZE_UPDATE_COLUMNS,
                replace_filters={"trade_date": payload.business_date},
            ),
            rows_rejected=rejected,
        )


def _etf_row(source: RawRow) -> PreparedRow:
    source_exchange = required_text(source.get("exchange"), "exchange")
    exchange = {"SH": "SSE", "SZ": "SZSE"}.get(source_exchange)
    if exchange is None:
        raise ProcessingError(f"unsupported ETF exchange: {source_exchange}")
    list_status = required_text(source.get("list_status"), "list_status")
    if list_status not in {"L", "D", "P"}:
        raise ProcessingError(f"invalid ETF list_status: {list_status}")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "csname": optional_text(source.get("csname")),
        "extname": optional_text(source.get("extname")),
        "cname": optional_text(source.get("cname")),
        "index_code": optional_text(source.get("index_code")),
        "index_name": optional_text(source.get("index_name")),
        "setup_date": optional_yyyymmdd(source.get("setup_date"), "setup_date"),
        "list_date": optional_yyyymmdd(source.get("list_date"), "list_date"),
        "list_status": list_status,
        "exchange": exchange,
        "source_exchange": source_exchange,
        "mgr_name": optional_text(source.get("mgr_name")),
        "custod_name": optional_text(source.get("custod_name")),
        "mgt_fee": decimal_value(source.get("mgt_fee"), "mgt_fee"),
        "etf_type": optional_text(source.get("etf_type")),
    }


def _etf_daily_row(
    source: RawRow,
    factor: RawRow | None,
    business_date: date | None,
) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "etf_daily")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "open": decimal_value(source.get("open"), "open", required=True),
        "high": decimal_value(source.get("high"), "high", required=True),
        "low": decimal_value(source.get("low"), "low", required=True),
        "close": decimal_value(source.get("close"), "close", required=True),
        "pre_close": decimal_value(source.get("pre_close"), "pre_close", required=True),
        "change": decimal_value(source.get("change"), "change", required=True),
        "pct_chg": decimal_value(source.get("pct_chg"), "pct_chg", required=True),
        "volume": scaled_decimal(source.get("vol"), "vol", 100, required=True),
        "amount": scaled_decimal(source.get("amount"), "amount", 1_000, required=True),
        "adj_factor": (
            None
            if factor is None
            else decimal_value(factor.get("adj_factor"), "adj_factor", required=True)
        ),
    }


def _etf_share_size_row(
    source: RawRow,
    business_date: date | None,
) -> PreparedRow | None:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "etf_share_size_daily")
    exchange = required_text(source.get("exchange"), "exchange")
    if exchange not in {"SSE", "SZSE", "BSE"}:
        raise ProcessingError(f"unsupported ETF share-size exchange: {exchange}")
    if source.get("total_share") in {None, ""} or source.get("total_size") in {
        None,
        "",
    }:
        return None
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "etf_name": optional_text(source.get("etf_name")),
        "total_share": scaled_decimal(
            source.get("total_share"),
            "total_share",
            10_000,
            required=True,
        ),
        "total_size": scaled_decimal(
            source.get("total_size"),
            "total_size",
            10_000,
            required=True,
        ),
        "nav": decimal_value(source.get("nav"), "nav"),
        "close": decimal_value(source.get("close"), "close"),
        "exchange": exchange,
    }


def _key_by_security_date(
    rows: tuple[RawRow, ...],
    business_date: date | None,
    dataset: str,
) -> dict[tuple[str, date], RawRow]:
    result: dict[tuple[str, date], RawRow] = {}
    for row in rows:
        ts_code = required_text(row.get("ts_code"), "ts_code")
        trade_date = yyyymmdd(row.get("trade_date"), "trade_date")
        require_business_date(trade_date, business_date, dataset)
        result[(ts_code, trade_date)] = row
    return result


def _single_business_date(rows: tuple[PreparedRow, ...], dataset: str) -> date:
    dates = {cast(date, row["trade_date"]) for row in rows}
    if len(dates) != 1:
        raise ProcessingError(f"{dataset} contains multiple business dates: {dates}")
    return dates.pop()


def _filter_to_etf_master(
    session: Session,
    rows: tuple[PreparedRow, ...],
) -> tuple[tuple[PreparedRow, ...], int]:
    codes = {cast(str, row["ts_code"]) for row in rows}
    existing = set(session.scalars(select(Etf.ts_code).where(Etf.ts_code.in_(codes))))
    matched = tuple(row for row in rows if row["ts_code"] in existing)
    return matched, len(rows) - len(matched)

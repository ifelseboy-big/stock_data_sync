from datetime import date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.bse_codes import canonical_stock_code
from app.catalog.tushare import (
    ADJ_FACTOR_SPEC,
    DAILY_BASIC_SPEC,
    DAILY_SPEC,
    MONEYFLOW_SPEC,
    STK_FACTOR_SPEC,
    STK_LIMIT_SPEC,
    SUSPEND_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.processors.raw_reader import RawRow, read_raw_assets
from app.modules.processing.processors.transforms import (
    decimal_value,
    optional_text,
    require_business_date,
    required_text,
    scaled_decimal,
    yyyymmdd,
)
from app.modules.processing.staging import PostgresStagingPublisher, PreparedRow
from app.modules.stocks.models import (
    Stock,
    StockDaily,
    StockMoneyflowDaily,
    StockSuspendDaily,
    StockTechnicalDaily,
)
from app.storage import RawAssetStore

DAILY_KEY = ("ts_code", "trade_date")
CORE_UPDATE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "volume",
    "amount",
    "after_hours_volume",
    "after_hours_amount",
    "adj_factor",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
    "limit_status",
    "up_limit",
    "down_limit",
    "synced_at",
)
TECHNICAL_FIELDS = (
    "open_hfq",
    "open_qfq",
    "close_hfq",
    "close_qfq",
    "high_hfq",
    "high_qfq",
    "low_hfq",
    "low_qfq",
    "pre_close_hfq",
    "pre_close_qfq",
    "macd_dif",
    "macd_dea",
    "macd",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "rsi_6",
    "rsi_12",
    "rsi_24",
    "boll_upper",
    "boll_mid",
    "boll_lower",
    "cci",
)
MONEYFLOW_FIELDS = (
    "buy_sm_vol",
    "sell_sm_vol",
    "buy_md_vol",
    "sell_md_vol",
    "buy_lg_vol",
    "sell_lg_vol",
    "buy_elg_vol",
    "sell_elg_vol",
    "net_mf_vol",
    "buy_sm_amount",
    "sell_sm_amount",
    "buy_md_amount",
    "sell_md_amount",
    "buy_lg_amount",
    "sell_lg_amount",
    "buy_elg_amount",
    "sell_elg_amount",
    "net_mf_amount",
)


class StockDailyCoreProcessor:
    name = "stock_daily_core"

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
            (DAILY_SPEC, DAILY_BASIC_SPEC, ADJ_FACTOR_SPEC),
        )
        daily_rows, daily_rejected, daily_warnings = _normalize_stock_code_rows(
            raw.rows_by_api["daily"],
            api_name="daily",
            key_fields=("trade_date",),
            matching_fields=tuple(field for field in DAILY_SPEC.fields if field != "ts_code"),
        )
        basic_rows, basic_rejected, basic_warnings = _normalize_stock_code_rows(
            raw.rows_by_api["daily_basic"],
            api_name="daily_basic",
            key_fields=("trade_date",),
            matching_fields=tuple(
                field for field in DAILY_BASIC_SPEC.fields if field != "ts_code"
            ),
        )
        factor_rows, factor_rejected, factor_warnings = _normalize_stock_code_rows(
            raw.rows_by_api["adj_factor"],
            api_name="adj_factor",
            key_fields=("trade_date",),
            matching_fields=("trade_date", "adj_factor"),
        )
        daily = _key_by_security_date(daily_rows, task.business_date, self.name)
        daily_basic = _key_by_security_date(
            basic_rows, task.business_date, self.name
        )
        adj_factor = _key_by_security_date(
            factor_rows, task.business_date, self.name
        )
        _require_same_keys("daily", daily, "daily_basic", daily_basic)
        _require_key_coverage("adj_factor", adj_factor, "daily", daily)
        rows = tuple(
            _stock_daily_core_row(
                daily[key],
                daily_basic[key],
                adj_factor[key],
                task.business_date,
            )
            for key in sorted(daily)
        )
        if not rows:
            raise ProcessingError("stock_daily.core cannot publish an empty trading day")
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=(
                daily_rejected
                + basic_rejected
                + factor_rejected
                + len(adj_factor)
                - len(daily)
            ),
            warning_messages=(*daily_warnings, *basic_warnings, *factor_warnings),
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
        _validate_stock_codes(session, rows)
        values = tuple({**row, "synced_at": published_at} for row in rows)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=DAILY_KEY,
                update_columns=CORE_UPDATE_COLUMNS,
                replace_filters={"trade_date": business_date},
            )
        )


class StockDailyLimitProcessor:
    name = "stock_daily_limit"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        raw = read_raw_assets(dependencies, asset_store, (STK_LIMIT_SPEC,))
        normalized, rows_rejected, warning_messages = _normalize_stock_code_rows(
            raw.rows_by_api["stk_limit"],
            api_name="stk_limit",
            key_fields=("trade_date",),
            matching_fields=("trade_date", "pre_close", "up_limit", "down_limit"),
        )
        rows = tuple(
            _stock_limit_row(source, task.business_date) for source in normalized
        )
        if not rows:
            raise ProcessingError("stock_daily.limit cannot publish an empty trading day")
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=rows_rejected,
            warning_messages=warning_messages,
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
        existing = {
            ts_code: pre_close
            for ts_code, pre_close in session.execute(
                select(StockDaily.ts_code, StockDaily.pre_close).where(
                    StockDaily.trade_date == business_date
                )
            )
        }
        matched_rows = tuple(row for row in rows if cast(str, row["ts_code"]) in existing)
        if not matched_rows:
            raise ProcessingError("stock_daily.limit has no rows matching stock_daily.core")
        for row in matched_rows:
            ts_code = cast(str, row["ts_code"])
            source_pre_close_value = row["source_pre_close"]
            if not isinstance(source_pre_close_value, Decimal):
                raise ProcessingError(f"stock_daily.limit has no pre_close for {ts_code}")
            source_pre_close = source_pre_close_value
            if abs(existing[ts_code] - source_pre_close) > Decimal("0.001"):
                raise ProcessingError(f"pre_close mismatch for {ts_code}")
        values = tuple(
            {
                "ts_code": row["ts_code"],
                "trade_date": row["trade_date"],
                "up_limit": row["up_limit"],
                "down_limit": row["down_limit"],
                "synced_at": published_at,
            }
            for row in matched_rows
        )
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockDaily.__table__),
                rows=values,
                strategy=WriteStrategy.PATCH_COLUMNS,
                key_columns=DAILY_KEY,
                update_columns=("up_limit", "down_limit", "synced_at"),
            ),
            rows_rejected=len(rows) - len(matched_rows),
        )


class StockTechnicalDailyProcessor:
    name = "stock_technical_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        raw = read_raw_assets(dependencies, asset_store, (STK_FACTOR_SPEC,))
        normalized, rows_rejected, warning_messages = _normalize_stock_code_rows(
            raw.rows_by_api["stk_factor"],
            api_name="stk_factor",
            key_fields=("trade_date",),
            matching_fields=(
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_change",
                "vol",
                "amount",
            ),
        )
        rows = tuple(
            _stock_technical_row(source, task.business_date) for source in normalized
        )
        if not rows:
            raise ProcessingError("stock_technical_daily cannot publish an empty trading day")
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=rows_rejected,
            warning_messages=warning_messages,
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
        _validate_stock_codes(session, rows)
        core_close = {
            ts_code: close
            for ts_code, close in session.execute(
                select(StockDaily.ts_code, StockDaily.close).where(
                    StockDaily.trade_date == business_date
                )
            )
        }
        for row in rows:
            ts_code = cast(str, row["ts_code"])
            if ts_code not in core_close:
                continue
            source_close = cast(Decimal, row["source_close"])
            if abs(core_close[ts_code] - source_close) > Decimal("0.001"):
                raise ProcessingError(f"technical/core close mismatch for {ts_code}")
        values = tuple(
            {
                key: value
                for key, value in {**row, "synced_at": published_at}.items()
                if key != "source_close"
            }
            for row in rows
        )
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockTechnicalDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=DAILY_KEY,
                update_columns=(*TECHNICAL_FIELDS, "synced_at"),
                replace_filters={"trade_date": business_date},
            )
        )


class StockMoneyflowDailyProcessor:
    name = "stock_moneyflow_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        raw = read_raw_assets(dependencies, asset_store, (MONEYFLOW_SPEC,))
        normalized, rows_rejected, warning_messages = _normalize_stock_code_rows(
            raw.rows_by_api["moneyflow"],
            api_name="moneyflow",
            key_fields=("trade_date",),
            matching_fields=tuple(
                field for field in MONEYFLOW_SPEC.fields if field != "ts_code"
            ),
        )
        rows = tuple(
            _moneyflow_row(source, task.business_date) for source in normalized
        )
        if not rows:
            raise ProcessingError("stock_moneyflow_daily cannot publish an empty trading day")
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=rows_rejected,
            warning_messages=warning_messages,
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
        _validate_stock_codes(session, rows)
        values = tuple({**row, "synced_at": published_at} for row in rows)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockMoneyflowDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=DAILY_KEY,
                update_columns=(*MONEYFLOW_FIELDS, "synced_at"),
                replace_filters={"trade_date": business_date},
            )
        )


class StockSuspendDailyProcessor:
    name = "stock_suspend_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        if task.business_date is None:
            raise ProcessingError("stock_suspend_daily requires a task business date")
        raw = read_raw_assets(dependencies, asset_store, (SUSPEND_SPEC,))
        normalized, rows_rejected, warning_messages = _normalize_stock_code_rows(
            raw.rows_by_api["suspend_d"],
            api_name="suspend_d",
            key_fields=("trade_date", "suspend_type"),
            matching_fields=("trade_date", "suspend_type", "suspend_timing"),
        )
        rows = tuple(
            _suspend_row(source, task.business_date) for source in normalized
        )
        return PreparedDataset(
            payload=(task.business_date, rows),
            rows_read=raw.row_count,
            rows_rejected=rows_rejected,
            warning_messages=warning_messages,
        )

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        business_date, raw_rows = cast(tuple[date, tuple[PreparedRow, ...]], prepared.payload)
        _validate_stock_codes(session, raw_rows)
        values = tuple({**row, "synced_at": published_at} for row in raw_rows)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, StockSuspendDaily.__table__),
                rows=values,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=("ts_code", "trade_date", "suspend_type"),
                update_columns=("suspend_timing", "synced_at"),
                replace_filters={"trade_date": business_date},
            )
        )


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


def _normalize_stock_code_rows(
    rows: tuple[RawRow, ...],
    *,
    api_name: str,
    key_fields: tuple[str, ...],
    matching_fields: tuple[str, ...],
) -> tuple[tuple[RawRow, ...], int, tuple[str, ...]]:
    normalized: dict[tuple[object, ...], tuple[RawRow, bool]] = {}
    mapped: dict[str, str] = {}
    duplicate_count = 0

    for source in rows:
        old_code = required_text(source.get("ts_code"), "ts_code")
        new_code = canonical_stock_code(old_code)
        is_alias = new_code != old_code
        row = {**source, "ts_code": new_code} if is_alias else source
        if is_alias:
            mapped[old_code] = new_code

        key = (new_code, *(row.get(field) for field in key_fields))
        previous = normalized.get(key)
        if previous is None:
            normalized[key] = (row, is_alias)
            continue

        previous_row, previous_is_alias = previous
        mismatched = tuple(
            field
            for field in matching_fields
            if previous_row.get(field) != row.get(field)
        )
        if mismatched:
            raise ProcessingError(
                f"{api_name} stock code alias conflict for {new_code}; "
                f"fields={mismatched[:5]}"
            )
        duplicate_count += 1
        if previous_is_alias and not is_alias:
            normalized[key] = (row, False)

    if not mapped:
        return tuple(row for row, _ in normalized.values()), duplicate_count, ()

    examples = ", ".join(f"{old}->{new}" for old, new in sorted(mapped.items())[:5])
    warning = f"{api_name} 已将 {len(mapped)} 个证券历史代码映射为现行代码"
    if duplicate_count:
        warning += f"，并去除 {duplicate_count} 条新旧代码重复记录"
    warning += f"（示例：{examples}）"
    return (
        tuple(row for row, _ in normalized.values()),
        duplicate_count,
        (warning,),
    )


def _require_same_keys(
    left_name: str,
    left: dict[tuple[str, date], RawRow],
    right_name: str,
    right: dict[tuple[str, date], RawRow],
) -> None:
    if left.keys() == right.keys():
        return
    missing = sorted(set(left) - set(right))[:5]
    extra = sorted(set(right) - set(left))[:5]
    raise ProcessingError(
        f"{left_name}/{right_name} key mismatch; missing={missing}, extra={extra}"
    )


def _require_key_coverage(
    superset_name: str,
    superset: dict[tuple[str, date], RawRow],
    required_name: str,
    required: dict[tuple[str, date], RawRow],
) -> None:
    missing = sorted(set(required) - set(superset))[:5]
    if missing:
        raise ProcessingError(f"{superset_name} does not cover {required_name}; missing={missing}")


def _stock_daily_core_row(
    daily: RawRow,
    basic: RawRow,
    factor: RawRow,
    business_date: date | None,
) -> PreparedRow:
    trade_date = yyyymmdd(daily.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_daily.core")
    daily_close = cast(Decimal, decimal_value(daily.get("close"), "daily.close", required=True))
    basic_close = cast(
        Decimal,
        decimal_value(basic.get("close"), "daily_basic.close", required=True),
    )
    if abs(daily_close - basic_close) > Decimal("0.001"):
        raise ProcessingError(f"daily/daily_basic close mismatch for {daily.get('ts_code')}")
    return {
        "ts_code": required_text(daily.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "open": decimal_value(daily.get("open"), "open", required=True),
        "high": decimal_value(daily.get("high"), "high", required=True),
        "low": decimal_value(daily.get("low"), "low", required=True),
        "close": daily_close,
        "pre_close": decimal_value(daily.get("pre_close"), "pre_close", required=True),
        "change": decimal_value(daily.get("change"), "change", required=True),
        "pct_chg": decimal_value(daily.get("pct_chg"), "pct_chg", required=True),
        "volume": scaled_decimal(daily.get("vol"), "vol", 100, required=True),
        "amount": scaled_decimal(daily.get("amount"), "amount", 1_000, required=True),
        "after_hours_volume": scaled_decimal(daily.get("ah_vol"), "ah_vol", 100),
        "after_hours_amount": scaled_decimal(daily.get("ah_amount"), "ah_amount", 1_000),
        "adj_factor": decimal_value(factor.get("adj_factor"), "adj_factor", required=True),
        "turnover_rate": decimal_value(basic.get("turnover_rate"), "turnover_rate"),
        "turnover_rate_f": decimal_value(basic.get("turnover_rate_f"), "turnover_rate_f"),
        "volume_ratio": decimal_value(basic.get("volume_ratio"), "volume_ratio"),
        "pe": decimal_value(basic.get("pe"), "pe"),
        "pe_ttm": decimal_value(basic.get("pe_ttm"), "pe_ttm"),
        "pb": decimal_value(basic.get("pb"), "pb"),
        "ps": decimal_value(basic.get("ps"), "ps"),
        "ps_ttm": decimal_value(basic.get("ps_ttm"), "ps_ttm"),
        "dv_ratio": decimal_value(basic.get("dv_ratio"), "dv_ratio"),
        "dv_ttm": decimal_value(basic.get("dv_ttm"), "dv_ttm"),
        "total_share": scaled_decimal(basic.get("total_share"), "total_share", 10_000),
        "float_share": scaled_decimal(basic.get("float_share"), "float_share", 10_000),
        "free_share": scaled_decimal(basic.get("free_share"), "free_share", 10_000),
        "total_mv": scaled_decimal(basic.get("total_mv"), "total_mv", 10_000),
        "circ_mv": scaled_decimal(basic.get("circ_mv"), "circ_mv", 10_000),
        "limit_status": None,
        "up_limit": None,
        "down_limit": None,
    }


def _stock_limit_row(source: RawRow, business_date: date | None) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_daily.limit")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "source_pre_close": decimal_value(source.get("pre_close"), "pre_close"),
        "up_limit": decimal_value(source.get("up_limit"), "up_limit"),
        "down_limit": decimal_value(source.get("down_limit"), "down_limit"),
    }


def _stock_technical_row(source: RawRow, business_date: date | None) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_technical_daily")
    row: PreparedRow = {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "source_close": decimal_value(source.get("close"), "close", required=True),
    }
    row.update({field: decimal_value(source.get(field), field) for field in TECHNICAL_FIELDS})
    return row


def _moneyflow_row(source: RawRow, business_date: date | None) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_moneyflow_daily")
    row: PreparedRow = {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
    }
    row.update(
        {
            field: scaled_decimal(
                source.get(field),
                field,
                100 if field.endswith("_vol") else 10_000,
            )
            for field in MONEYFLOW_FIELDS
        }
    )
    return row


def _suspend_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_suspend_daily")
    suspend_type = required_text(source.get("suspend_type"), "suspend_type")
    if suspend_type not in {"S", "R"}:
        raise ProcessingError(f"invalid suspend_type: {suspend_type}")
    return {
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "suspend_type": suspend_type,
        "suspend_timing": optional_text(source.get("suspend_timing")),
    }


def _single_business_date(rows: tuple[PreparedRow, ...], dataset: str) -> date:
    dates = {cast(date, row["trade_date"]) for row in rows}
    if len(dates) != 1:
        raise ProcessingError(f"{dataset} must contain exactly one business date")
    return dates.pop()


def _validate_stock_codes(session: Session, rows: tuple[PreparedRow, ...]) -> None:
    codes = {cast(str, row["ts_code"]) for row in rows}
    if not codes:
        return
    existing = set(session.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(codes))))
    missing = codes - existing
    if missing:
        raise ProcessingError(f"dataset references unknown stocks: {sorted(missing)[:5]}")

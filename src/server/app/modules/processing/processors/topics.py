import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import Table, delete, insert, select
from sqlalchemy.orm import Session

from app.catalog import ApiSpec, WriteStrategy
from app.catalog.tushare import (
    DC_CONCEPT_CONS_SPEC,
    DC_CONCEPT_SPEC,
    DC_HOT_SPEC,
    LIMIT_LIST_SPEC,
    LIMIT_STEP_SPEC,
    THS_DAILY_SPEC,
    THS_HOT_SPEC,
    THS_INDEX_SPEC,
    THS_MEMBER_SPEC,
    TOP_INST_SPEC,
    TOP_LIST_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.processors.raw_reader import RawRow, read_raw_assets
from app.modules.processing.processors.transforms import (
    decimal_value,
    integer_value,
    optional_text,
    optional_yyyymmdd,
    require_business_date,
    required_text,
    scaled_decimal,
    yyyymmdd,
)
from app.modules.processing.staging import PostgresStagingPublisher, PreparedRow
from app.modules.stocks.models import Stock
from app.modules.topics.models import (
    ConceptBoard,
    ConceptBoardDaily,
    ConceptBoardMember,
    MarketThemeDaily,
    MarketThemeMemberDaily,
    StockHotRankDaily,
    StockLimitEventDaily,
    StockLimitStepDaily,
    StockTopInstDaily,
    StockTopListDaily,
    ThemeIndex,
    ThemeIndexDaily,
    ThemeIndexMember,
)
from app.storage import RawAssetStore

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class DatedRows:
    business_date: date
    rows: tuple[PreparedRow, ...]


class ConceptBoardProcessor:
    name = "concept_board"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (THS_INDEX_SPEC,))
        rows = tuple(
            row
            for source in raw.rows_by_api["ths_index"]
            if (row := _concept_board_row(source)) is not None
        )
        if not rows:
            raise ProcessingError("concept_board cannot be empty")
        return PreparedDataset(rows, raw.row_count, raw.row_count - len(rows))

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        rows = _with_synced_at(cast(tuple[PreparedRow, ...], prepared.payload), published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ConceptBoard.__table__),
            rows=rows,
            strategy=WriteStrategy.MASTER_MERGE,
            key_columns=("source", "ts_code"),
            update_columns=(
                "name",
                "member_count",
                "exchange",
                "list_date",
                "board_type",
                "synced_at",
            ),
        )
        return PublicationResult(written)


class ConceptBoardDailyProcessor:
    name = "concept_board_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (THS_DAILY_SPEC,))
        rows = tuple(
            _concept_board_daily_row(row, business_date) for row in raw.rows_by_api["ths_daily"]
        )
        return PreparedDataset(DatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(DatedRows, prepared.payload)
        boards = _concept_board_codes(session)
        matched = tuple(row for row in payload.rows if row["ts_code"] in boards)
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ConceptBoardDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("source", "ts_code", "trade_date"),
            update_columns=(
                "close",
                "open",
                "high",
                "low",
                "pre_close",
                "avg_price",
                "change",
                "pct_change",
                "volume",
                "turnover_rate",
                "total_mv",
                "float_mv",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class ConceptBoardMemberProcessor:
    name = "concept_board_member"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        observed_at = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (THS_MEMBER_SPEC,))
        rows = tuple(
            row
            for source in raw.rows_by_api["ths_member"]
            if (row := _concept_board_member_row(source, observed_at)) is not None
        )
        return PreparedDataset(
            payload=rows,
            rows_read=raw.row_count,
            rows_rejected=raw.row_count - len(rows),
        )

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        source_rows = cast(tuple[PreparedRow, ...], prepared.payload)
        boards = _concept_board_codes(session)
        stocks = _stock_codes(session, {cast(str, row["con_code"]) for row in source_rows})
        matched = tuple(
            row for row in source_rows if row["ts_code"] in boards and row["con_code"] in stocks
        )
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ConceptBoardMember.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_ENTITY,
            key_columns=("source", "ts_code", "con_code"),
            update_columns=(
                "con_name",
                "weight",
                "in_date",
                "out_date",
                "is_current",
                "observed_at",
                "synced_at",
            ),
            replace_filters={"source": "THS"},
        )
        return PublicationResult(written, len(source_rows) - len(matched))


class ThemeIndexProcessor:
    name = "theme_index"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        raw = read_raw_assets(dependencies, asset_store, (THS_INDEX_SPEC,))
        rows = tuple(
            row
            for source in raw.rows_by_api["ths_index"]
            if (row := _theme_index_row(source)) is not None
        )
        if not rows:
            raise ProcessingError("theme_index cannot be empty")
        return PreparedDataset(rows, raw.row_count, raw.row_count - len(rows))

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        rows = _with_synced_at(cast(tuple[PreparedRow, ...], prepared.payload), published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ThemeIndex.__table__),
            rows=rows,
            strategy=WriteStrategy.MASTER_MERGE,
            key_columns=("source", "ts_code"),
            update_columns=(
                "name",
                "member_count",
                "exchange",
                "list_date",
                "theme_type",
                "synced_at",
            ),
        )
        return PublicationResult(written)


class ThemeIndexDailyProcessor:
    name = "theme_index_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (THS_DAILY_SPEC,))
        rows = tuple(
            _concept_board_daily_row(row, business_date) for row in raw.rows_by_api["ths_daily"]
        )
        return PreparedDataset(DatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(DatedRows, prepared.payload)
        theme_codes = _theme_index_codes(session)
        matched = tuple(row for row in payload.rows if row["ts_code"] in theme_codes)
        if not matched:
            raise ProcessingError("theme_index_daily has no rows matching theme_index")
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ThemeIndexDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("source", "ts_code", "trade_date"),
            update_columns=(
                "close",
                "open",
                "high",
                "low",
                "pre_close",
                "avg_price",
                "change",
                "pct_change",
                "volume",
                "turnover_rate",
                "total_mv",
                "float_mv",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class ThemeIndexMemberProcessor:
    name = "theme_index_member"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        observed_at = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (THS_MEMBER_SPEC,))
        rows = tuple(
            row
            for source in raw.rows_by_api["ths_member"]
            if (row := _concept_board_member_row(source, observed_at)) is not None
        )
        return PreparedDataset(rows, raw.row_count, raw.row_count - len(rows))

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        source_rows = cast(tuple[PreparedRow, ...], prepared.payload)
        theme_codes = _theme_index_codes(session)
        stocks = _stock_codes(session, {cast(str, row["con_code"]) for row in source_rows})
        matched = tuple(
            row
            for row in source_rows
            if row["ts_code"] in theme_codes and row["con_code"] in stocks
        )
        if not matched:
            raise ProcessingError("theme_index_member has no rows matching theme_index")
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, ThemeIndexMember.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_ENTITY,
            key_columns=("source", "ts_code", "con_code"),
            update_columns=(
                "con_name",
                "weight",
                "in_date",
                "out_date",
                "is_current",
                "observed_at",
                "synced_at",
            ),
            replace_filters={"source": "THS"},
        )
        return PublicationResult(written, len(source_rows) - len(matched))


class StockHotRankDailyProcessor:
    name = "stock_hot_rank_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        rows: list[PreparedRow] = []
        rows_read = 0
        for dependency in dependencies:
            if dependency.dependency_name == "ths_hot":
                raw = read_raw_assets((dependency,), asset_store, (THS_HOT_SPEC,))
                source_rows = raw.rows_by_api["ths_hot"]
                rows.extend(
                    _hot_rank_row(row, business_date, source="THS", rank_type="FINAL")
                    for row in source_rows
                )
                rows_read += raw.row_count
            elif dependency.dependency_name == "dc_hot":
                raw = read_raw_assets((dependency,), asset_store, (DC_HOT_SPEC,))
                source_rows = raw.rows_by_api["dc_hot"]
                rank_type = _scope_value(dependency.scope_key, "hot_type")
                rows.extend(
                    _hot_rank_row(row, business_date, source="DC", rank_type=rank_type)
                    for row in source_rows
                )
                rows_read += raw.row_count
        if rows_read == 0:
            raise ProcessingError("stock_hot_rank_daily cannot be empty")
        return PreparedDataset(DatedRows(business_date, tuple(rows)), rows_read)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(DatedRows, prepared.payload)
        stocks = _stock_codes(session, {cast(str, row["ts_code"]) for row in payload.rows})
        matched = tuple(row for row in payload.rows if row["ts_code"] in stocks)
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, StockHotRankDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("source", "trade_date", "market_type", "rank_type", "ts_code"),
            update_columns=(
                "data_type",
                "ts_name",
                "rank",
                "pct_change",
                "current_price",
                "concept",
                "rank_reason",
                "hot",
                "rank_time",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class MarketThemeDailyProcessor:
    name = "market_theme_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (DC_CONCEPT_SPEC,))
        rows = tuple(_theme_daily_row(row, business_date) for row in raw.rows_by_api["dc_concept"])
        return PreparedDataset(DatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(DatedRows, prepared.payload)
        rows = _with_synced_at(payload.rows, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, MarketThemeDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("source", "theme_code", "trade_date"),
            update_columns=(
                "name",
                "pct_change",
                "hot",
                "rank",
                "strength",
                "z_t_num",
                "main_change",
                "lead_stock",
                "lead_stock_code",
                "lead_stock_pct_change",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written)


class MarketThemeMemberDailyProcessor:
    name = "market_theme_member_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        business_date = _business_date(task, self.name)
        raw = read_raw_assets(dependencies, asset_store, (DC_CONCEPT_CONS_SPEC,))
        rows = tuple(
            _theme_member_row(row, business_date) for row in raw.rows_by_api["dc_concept_cons"]
        )
        return PreparedDataset(DatedRows(business_date, rows), raw.row_count)

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload = cast(DatedRows, prepared.payload)
        theme_codes = set(
            session.scalars(
                select(MarketThemeDaily.theme_code).where(
                    MarketThemeDaily.trade_date == payload.business_date,
                    MarketThemeDaily.source == "DC",
                )
            )
        )
        stocks = _stock_codes(session, {cast(str, row["ts_code"]) for row in payload.rows})
        matched = tuple(
            row
            for row in payload.rows
            if row["theme_code"] in theme_codes and row["ts_code"] in stocks
        )
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, MarketThemeMemberDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("source", "trade_date", "theme_code", "ts_code"),
            update_columns=("name", "industry_code", "industry", "reason", "hot_num", "synced_at"),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class StockTopListDailyProcessor:
    name = "stock_top_list_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        prepared = _prepare_daily_rows(
            task,
            dependencies,
            asset_store,
            TOP_LIST_SPEC,
            self.name,
            _top_list_row,
        )
        payload = cast(DatedRows, prepared.payload)
        rows, warning_messages = _deduplicate_top_list_rows(payload.rows)
        return PreparedDataset(
            DatedRows(payload.business_date, rows),
            prepared.rows_read,
            prepared.rows_read - len(rows),
            warning_messages,
        )

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_daily_stocks(session, prepared)
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, StockTopListDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("trade_date", "ts_code", "reason"),
            update_columns=(
                "name",
                "close",
                "pct_change",
                "turnover_rate",
                "amount",
                "l_sell",
                "l_buy",
                "l_amount",
                "net_amount",
                "net_rate",
                "amount_rate",
                "float_values",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class StockTopInstDailyProcessor:
    name = "stock_top_inst_daily"

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        return _prepare_daily_rows(
            task,
            dependencies,
            asset_store,
            TOP_INST_SPEC,
            self.name,
            _top_inst_row,
        )

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_daily_stocks(session, prepared)
        rows = _with_synced_at(matched, published_at)
        session.execute(
            delete(StockTopInstDaily).where(StockTopInstDaily.trade_date == payload.business_date)
        )
        if rows:
            session.execute(insert(StockTopInstDaily), rows)
        return PublicationResult(len(rows), len(payload.rows) - len(matched))


class StockLimitEventDailyProcessor:
    name = "stock_limit_event_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        return _prepare_daily_rows(
            task,
            dependencies,
            asset_store,
            LIMIT_LIST_SPEC,
            self.name,
            _limit_event_row,
        )

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_daily_stocks(session, prepared)
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, StockLimitEventDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("trade_date", "ts_code", "limit_type"),
            update_columns=(
                "industry",
                "name",
                "close",
                "pct_chg",
                "amount_raw",
                "limit_amount_raw",
                "float_mv_raw",
                "total_mv_raw",
                "turnover_ratio",
                "fd_amount_raw",
                "first_time",
                "last_time",
                "open_times",
                "up_stat",
                "limit_times",
                "synced_at",
            ),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


class StockLimitStepDailyProcessor:
    name = "stock_limit_step_daily"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        return _prepare_daily_rows(
            task,
            dependencies,
            asset_store,
            LIMIT_STEP_SPEC,
            self.name,
            _limit_step_row,
        )

    def write(
        self, session: Session, prepared: PreparedDataset, *, published_at: datetime
    ) -> PublicationResult:
        payload, matched = _filter_daily_stocks(session, prepared)
        rows = _with_synced_at(matched, published_at)
        written = self._publisher.publish(
            session,
            target=cast(Table, StockLimitStepDaily.__table__),
            rows=rows,
            strategy=WriteStrategy.REPLACE_DATE,
            key_columns=("trade_date", "ts_code"),
            update_columns=("name", "nums", "synced_at"),
            replace_filters={"trade_date": payload.business_date},
        )
        return PublicationResult(written, len(payload.rows) - len(matched))


def _prepare_daily_rows(
    task: ClaimedProcessingTask,
    dependencies: tuple[RawDependencyAsset, ...],
    asset_store: RawAssetStore,
    spec: ApiSpec,
    dataset: str,
    transform: Callable[[RawRow, date], PreparedRow],
) -> PreparedDataset:
    business_date = _business_date(task, dataset)
    raw = read_raw_assets(dependencies, asset_store, (spec,))
    rows = tuple(transform(row, business_date) for row in raw.rows_by_api[spec.api_name])
    return PreparedDataset(DatedRows(business_date, rows), raw.row_count)


def _filter_daily_stocks(
    session: Session, prepared: PreparedDataset
) -> tuple[DatedRows, tuple[PreparedRow, ...]]:
    payload = cast(DatedRows, prepared.payload)
    stocks = _stock_codes(session, {cast(str, row["ts_code"]) for row in payload.rows})
    return payload, tuple(row for row in payload.rows if row["ts_code"] in stocks)


def _concept_board_row(source: RawRow) -> PreparedRow | None:
    board_type = required_text(source.get("type"), "type")
    if board_type != "N":
        return None
    return {
        "source": "THS",
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": required_text(source.get("name"), "name"),
        "member_count": integer_value(source.get("count"), "count"),
        "exchange": optional_text(source.get("exchange")),
        "list_date": optional_yyyymmdd(source.get("list_date"), "list_date"),
        "board_type": board_type,
    }


def _theme_index_row(source: RawRow) -> PreparedRow | None:
    theme_type = required_text(source.get("type"), "type")
    if theme_type != "TH":
        return None
    return {
        "source": "THS",
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": required_text(source.get("name"), "name"),
        "member_count": integer_value(source.get("count"), "count"),
        "exchange": optional_text(source.get("exchange")),
        "list_date": optional_yyyymmdd(source.get("list_date"), "list_date"),
        "theme_type": theme_type,
    }


def _concept_board_daily_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "concept_board_daily")
    return {
        "source": "THS",
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "trade_date": trade_date,
        "close": decimal_value(source.get("close"), "close", required=True),
        "open": decimal_value(source.get("open"), "open"),
        "high": decimal_value(source.get("high"), "high"),
        "low": decimal_value(source.get("low"), "low"),
        "pre_close": decimal_value(source.get("pre_close"), "pre_close"),
        "avg_price": decimal_value(source.get("avg_price"), "avg_price"),
        "change": decimal_value(source.get("change"), "change"),
        "pct_change": decimal_value(source.get("pct_change"), "pct_change"),
        "volume": scaled_decimal(source.get("vol"), "vol", 100),
        "turnover_rate": decimal_value(source.get("turnover_rate"), "turnover_rate"),
        "total_mv": decimal_value(source.get("total_mv"), "total_mv"),
        "float_mv": decimal_value(source.get("float_mv"), "float_mv"),
    }


def _concept_board_member_row(source: RawRow, observed_at: date) -> PreparedRow | None:
    if required_text(source.get("is_new"), "is_new") != "Y":
        return None
    return {
        "source": "THS",
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "con_code": required_text(source.get("con_code"), "con_code"),
        "con_name": optional_text(source.get("con_name")),
        "weight": decimal_value(source.get("weight"), "weight"),
        "in_date": optional_yyyymmdd(source.get("in_date"), "in_date"),
        "out_date": optional_yyyymmdd(source.get("out_date"), "out_date"),
        "is_current": True,
        "observed_at": observed_at,
    }


def _hot_rank_row(
    source_row: RawRow,
    business_date: date,
    *,
    source: str,
    rank_type: str,
) -> PreparedRow:
    trade_date = yyyymmdd(source_row.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_hot_rank_daily")
    return {
        "source": source,
        "trade_date": trade_date,
        "market_type": "A股" if source == "THS" else "A股市场",
        "rank_type": rank_type,
        "data_type": optional_text(source_row.get("data_type")),
        "ts_code": required_text(source_row.get("ts_code"), "ts_code"),
        "ts_name": optional_text(source_row.get("ts_name")),
        "rank": integer_value(source_row.get("rank"), "rank"),
        "pct_change": decimal_value(source_row.get("pct_change"), "pct_change"),
        "current_price": decimal_value(source_row.get("current_price"), "current_price"),
        "concept": _json_value(source_row.get("concept")),
        "rank_reason": optional_text(source_row.get("rank_reason")),
        "hot": decimal_value(source_row.get("hot"), "hot"),
        "rank_time": _rank_time(source_row.get("rank_time"), trade_date),
    }


def _theme_daily_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "market_theme_daily")
    return {
        "source": "DC",
        "theme_code": required_text(source.get("theme_code"), "theme_code"),
        "trade_date": trade_date,
        "name": required_text(source.get("name"), "name"),
        "pct_change": decimal_value(source.get("pct_change"), "pct_change"),
        "hot": decimal_value(source.get("hot"), "hot"),
        "rank": integer_value(source.get("sort"), "sort"),
        "strength": decimal_value(source.get("strength"), "strength"),
        "z_t_num": integer_value(source.get("z_t_num"), "z_t_num"),
        "main_change": decimal_value(source.get("main_change"), "main_change"),
        "lead_stock": optional_text(source.get("lead_stock")),
        "lead_stock_code": optional_text(source.get("lead_stock_code")),
        "lead_stock_pct_change": decimal_value(
            source.get("lead_stock_pct_change"), "lead_stock_pct_change"
        ),
    }


def _theme_member_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "market_theme_member_daily")
    return {
        "source": "DC",
        "trade_date": trade_date,
        "theme_code": required_text(source.get("theme_code"), "theme_code"),
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": optional_text(source.get("name")),
        "industry_code": optional_text(source.get("industry_code")),
        "industry": optional_text(source.get("industry")),
        "reason": optional_text(source.get("reason")),
        "hot_num": integer_value(source.get("hot_num"), "hot_num"),
    }


def _top_list_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_top_list_daily")
    return {
        "trade_date": trade_date,
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": optional_text(source.get("name")),
        "close": decimal_value(source.get("close"), "close"),
        "pct_change": decimal_value(source.get("pct_change"), "pct_change"),
        "turnover_rate": decimal_value(source.get("turnover_rate"), "turnover_rate"),
        "amount": decimal_value(source.get("amount"), "amount"),
        "l_sell": decimal_value(source.get("l_sell"), "l_sell"),
        "l_buy": decimal_value(source.get("l_buy"), "l_buy"),
        "l_amount": decimal_value(source.get("l_amount"), "l_amount"),
        "net_amount": decimal_value(source.get("net_amount"), "net_amount"),
        "net_rate": decimal_value(source.get("net_rate"), "net_rate"),
        "amount_rate": decimal_value(source.get("amount_rate"), "amount_rate"),
        "float_values": decimal_value(source.get("float_values"), "float_values"),
        "reason": required_text(source.get("reason"), "reason"),
    }


def _deduplicate_top_list_rows(
    rows: tuple[PreparedRow, ...],
) -> tuple[tuple[PreparedRow, ...], tuple[str, ...]]:
    unique: dict[tuple[object, object, object], PreparedRow] = {}
    warning_messages: list[str] = []
    for row in rows:
        key = (row["trade_date"], row["ts_code"], row["reason"])
        existing = unique.get(key)
        if existing is None:
            unique[key] = row
            continue
        if existing == row:
            continue
        if _row_is_compatible_subset(existing, row):
            kept, discarded = row, existing
            unique[key] = row
        elif _row_is_compatible_subset(row, existing):
            kept, discarded = existing, row
        else:
            raise ProcessingError(f"top_list contains conflicting duplicate key: {key}")
        missing_fields = tuple(
            field
            for field, value in discarded.items()
            if value is None and kept[field] is not None
        )
        warning_messages.append(
            "top_list 重复记录字段不完整，已保留较完整记录："
            f"日期 {key[0]}，股票 {key[1]}，上榜原因“{key[2]}”；"
            f"缺失字段：{', '.join(missing_fields)}"
        )
    return tuple(unique.values()), tuple(warning_messages)


def _row_is_compatible_subset(candidate: PreparedRow, complete: PreparedRow) -> bool:
    return all(value is None or value == complete[field] for field, value in candidate.items())


def _top_inst_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_top_inst_daily")
    side = integer_value(source.get("side"), "side")
    if side not in {0, 1}:
        raise ProcessingError(f"invalid top_inst side: {side}")
    return {
        "trade_date": trade_date,
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "exalter": required_text(source.get("exalter"), "exalter"),
        "side": side,
        "buy": decimal_value(source.get("buy"), "buy"),
        "buy_rate": decimal_value(source.get("buy_rate"), "buy_rate"),
        "sell": decimal_value(source.get("sell"), "sell"),
        "sell_rate": decimal_value(source.get("sell_rate"), "sell_rate"),
        "net_buy": decimal_value(source.get("net_buy"), "net_buy"),
        "reason": required_text(source.get("reason"), "reason"),
    }


def _limit_event_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_limit_event_daily")
    limit_type = required_text(source.get("limit"), "limit")
    if limit_type not in {"U", "D", "Z"}:
        raise ProcessingError(f"invalid limit type: {limit_type}")
    return {
        "trade_date": trade_date,
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "limit_type": limit_type,
        "industry": optional_text(source.get("industry")),
        "name": optional_text(source.get("name")),
        "close": decimal_value(source.get("close"), "close"),
        "pct_chg": decimal_value(source.get("pct_chg"), "pct_chg"),
        "amount_raw": decimal_value(source.get("amount"), "amount"),
        "limit_amount_raw": decimal_value(source.get("limit_amount"), "limit_amount"),
        "float_mv_raw": decimal_value(source.get("float_mv"), "float_mv"),
        "total_mv_raw": decimal_value(source.get("total_mv"), "total_mv"),
        "turnover_ratio": decimal_value(source.get("turnover_ratio"), "turnover_ratio"),
        "fd_amount_raw": decimal_value(source.get("fd_amount"), "fd_amount"),
        "first_time": _hhmmss(source.get("first_time"), "first_time"),
        "last_time": _hhmmss(source.get("last_time"), "last_time"),
        "open_times": integer_value(source.get("open_times"), "open_times"),
        "up_stat": optional_text(source.get("up_stat")),
        "limit_times": integer_value(source.get("limit_times"), "limit_times"),
    }


def _limit_step_row(source: RawRow, business_date: date) -> PreparedRow:
    trade_date = yyyymmdd(source.get("trade_date"), "trade_date")
    require_business_date(trade_date, business_date, "stock_limit_step_daily")
    nums = integer_value(source.get("nums"), "nums")
    if nums is None or nums <= 0:
        raise ProcessingError(f"invalid limit step count: {nums}")
    return {
        "trade_date": trade_date,
        "ts_code": required_text(source.get("ts_code"), "ts_code"),
        "name": optional_text(source.get("name")),
        "nums": nums,
    }


def _business_date(task: ClaimedProcessingTask, dataset: str) -> date:
    if task.business_date is None:
        raise ProcessingError(f"{dataset} requires a business date")
    return task.business_date


def _concept_board_codes(session: Session) -> set[str]:
    return set(session.scalars(select(ConceptBoard.ts_code).where(ConceptBoard.source == "THS")))


def _theme_index_codes(session: Session) -> set[str]:
    return set(session.scalars(select(ThemeIndex.ts_code).where(ThemeIndex.source == "THS")))


def _stock_codes(session: Session, codes: set[str]) -> set[str]:
    if not codes:
        return set()
    return set(session.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(codes))))


def _with_synced_at(
    rows: tuple[PreparedRow, ...], published_at: datetime
) -> tuple[PreparedRow, ...]:
    return tuple({**row, "synced_at": published_at} for row in rows)


def _scope_value(scope_key: str, key: str) -> str:
    for item in scope_key.split(";"):
        name, separator, value = item.partition("=")
        if separator and name == key:
            return value
    raise ProcessingError(f"scope {scope_key!r} is missing {key}")


def _json_value(value: object) -> dict[str, object] | list[object] | None:
    text = optional_text(value)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        normalized = text[1:-1] if text.startswith("{") and text.endswith("}") else text
        return [item.strip() for item in normalized.split(",") if item.strip()]
    if isinstance(parsed, (dict, list)):
        return cast(dict[str, object] | list[object], parsed)
    return [parsed]


def _rank_time(value: object, trade_date: date) -> datetime:
    text = optional_text(value)
    if text is None:
        return datetime.combine(trade_date, time(22, 30), SHANGHAI)
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d %H:%M:%S",
        "%Y%m%d%H%M%S",
        "%H:%M:%S",
        "%H%M%S",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if pattern in {"%H:%M:%S", "%H%M%S"}:
            parsed = datetime.combine(trade_date, parsed.time())
        return parsed.replace(tzinfo=SHANGHAI)
    raise ProcessingError(f"invalid rank_time: {text}")


def _hhmmss(value: object, field: str) -> time | None:
    text = optional_text(value)
    if text is None:
        return None
    normalized = text.replace(":", "")
    if len(normalized) < 6:
        normalized = normalized.zfill(6)
    try:
        return datetime.strptime(normalized, "%H%M%S").time()
    except ValueError as exc:
        raise ProcessingError(f"invalid {field}: {text}") from exc

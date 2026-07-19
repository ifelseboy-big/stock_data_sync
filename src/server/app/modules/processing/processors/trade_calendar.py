from datetime import date, datetime
from typing import cast

from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.catalog import WriteStrategy
from app.catalog.tushare import TRADE_CAL_SPEC
from app.common.errors import ProcessingError, RawAssetError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.staging import PostgresStagingPublisher
from app.modules.stocks.models import TradeCalendar
from app.storage import RawAssetMetadata, RawAssetStore, schema_fingerprint

type TradeCalendarRow = dict[str, object]


class TradeCalendarProcessor:
    name = "trade_calendar"

    def __init__(self) -> None:
        self._publisher = PostgresStagingPublisher()

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset:
        del task
        matching = tuple(
            dependency
            for dependency in dependencies
            if dependency.dependency_name == TRADE_CAL_SPEC.api_name
        )
        if not matching:
            raise ProcessingError("trade_calendar requires trade_cal raw assets")

        expected_fingerprint = schema_fingerprint(TRADE_CAL_SPEC.schema)
        rows: list[TradeCalendarRow] = []
        keys: set[tuple[str, date]] = set()
        observed_exchanges: set[str] = set()
        observed_years: set[int] = set()
        rows_read = 0

        for dependency in matching:
            if dependency.schema_fingerprint != expected_fingerprint:
                raise ProcessingError(
                    f"trade_cal schema mismatch for asset {dependency.asset_id}"
                )
            try:
                asset_store.verify(
                    RawAssetMetadata(
                        storage_uri=dependency.storage_uri,
                        content_hash=dependency.content_hash,
                        schema_fingerprint=dependency.schema_fingerprint,
                        row_count=dependency.row_count,
                        size_bytes=0,
                    )
                )
            except RawAssetError as exc:
                raise ProcessingError(str(exc), retryable=False) from exc

            for batch in asset_store.iter_batches(dependency.storage_uri):
                for source in batch.to_pylist():
                    rows_read += 1
                    exchange = _required_text(source.get("exchange"), "exchange")
                    cal_date = _parse_yyyymmdd(source.get("cal_date"), "cal_date")
                    is_open = _parse_is_open(source.get("is_open"))
                    pretrade_date = _parse_optional_yyyymmdd(source.get("pretrade_date"))
                    key = (exchange, cal_date)
                    if key in keys:
                        raise ProcessingError(
                            f"duplicate trade calendar key: {exchange}/{cal_date.isoformat()}"
                        )
                    keys.add(key)
                    observed_exchanges.add(exchange)
                    observed_years.add(cal_date.year)
                    rows.append(
                        {
                            "exchange": exchange,
                            "cal_date": cal_date,
                            "is_open": is_open,
                            "pretrade_date": pretrade_date,
                        }
                    )

        _validate_calendar_coverage(rows, observed_exchanges, observed_years)
        return PreparedDataset(payload=tuple(rows), rows_read=rows_read)

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        rows = cast(tuple[TradeCalendarRow, ...], prepared.payload)
        if not rows:
            raise ProcessingError("trade_calendar cannot publish an empty dataset")
        values = tuple({**row, "synced_at": published_at} for row in rows)
        return PublicationResult(
            self._publisher.publish(
                session,
                target=cast(Table, TradeCalendar.__table__),
                rows=values,
                strategy=WriteStrategy.MASTER_MERGE,
                key_columns=("exchange", "cal_date"),
                update_columns=("is_open", "pretrade_date", "synced_at"),
            )
        )


def _required_text(value: object, field: str) -> str:
    if value is None or not str(value).strip():
        raise ProcessingError(f"trade_calendar field {field} is required")
    return str(value).strip()


def _parse_yyyymmdd(value: object, field: str) -> date:
    text = _required_text(value, field)
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ProcessingError(f"invalid {field}: {text}") from exc


def _parse_optional_yyyymmdd(value: object) -> date | None:
    if value is None or not str(value).strip():
        return None
    return _parse_yyyymmdd(value, "pretrade_date")


def _parse_is_open(value: object) -> bool:
    if value in (0, 0.0, "0"):
        return False
    if value in (1, 1.0, "1"):
        return True
    raise ProcessingError(f"invalid is_open value: {value!r}")


def _validate_calendar_coverage(
    rows: list[TradeCalendarRow],
    exchanges: set[str],
    years: set[int],
) -> None:
    if exchanges != {"SSE", "SZSE"}:
        raise ProcessingError(
            f"trade_calendar requires SSE and SZSE, got {sorted(exchanges)}"
        )
    for exchange in exchanges:
        for year in years:
            dates = {
                cast(date, row["cal_date"])
                for row in rows
                if row["exchange"] == exchange
                and cast(date, row["cal_date"]).year == year
            }
            expected = date(year, 12, 31).timetuple().tm_yday
            if len(dates) != expected:
                raise ProcessingError(
                    f"trade_calendar coverage incomplete for {exchange}/{year}: "
                    f"expected {expected}, got {len(dates)}"
                )

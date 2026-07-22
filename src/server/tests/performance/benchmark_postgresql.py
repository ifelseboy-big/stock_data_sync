from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.catalog import WriteStrategy
from app.core.config import settings
from app.modules.acquisition.models import (
    BatchType,
    CollectionTask,
    CollectionTaskStatus,
)
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.operations.repository import OperationsRepository
from app.modules.partitions.service import ensure_monthly_partitions, month_start
from app.modules.processing.models import ProcessingTask, ProcessingTaskStatus
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.staging import PostgresStagingPublisher
from app.modules.stocks.models import StockDaily


@dataclass(frozen=True, slots=True)
class ScaleProfile:
    name: str
    stock_count: int
    trading_day_count: int
    batch_count: int
    active_batch_count: int
    collection_task_count: int
    processing_task_count: int
    provider_request_count: int
    release_count: int
    repetitions: int

    @property
    def business_row_count(self) -> int:
        return self.stock_count * self.trading_day_count


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    iterations: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    threshold_ms: float
    passed: bool


PROFILES = {
    "smoke": ScaleProfile(
        name="smoke",
        stock_count=100,
        trading_day_count=60,
        batch_count=100,
        active_batch_count=50,
        collection_task_count=2_500,
        processing_task_count=500,
        provider_request_count=5_000,
        release_count=250,
        repetitions=3,
    ),
    "operations": ScaleProfile(
        name="operations",
        stock_count=100,
        trading_day_count=60,
        batch_count=5_000,
        active_batch_count=5,
        collection_task_count=150_000,
        processing_task_count=30_000,
        provider_request_count=10_000,
        release_count=15_000,
        repetitions=5,
    ),
    "target": ScaleProfile(
        name="target",
        stock_count=5_500,
        trading_day_count=1_820,
        batch_count=20_000,
        active_batch_count=2_000,
        collection_task_count=500_000,
        processing_task_count=100_000,
        provider_request_count=1_000_000,
        release_count=50_000,
        repetitions=10,
    ),
}


def _run_step[T](name: str, action: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    value = action()
    elapsed = time.perf_counter() - started
    print(f"{name}: {elapsed:.2f}s", flush=True)
    return value, elapsed


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _measure(
    name: str,
    action: Callable[[], object],
    *,
    repetitions: int,
    threshold_ms: float,
) -> BenchmarkResult:
    action()
    durations: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        action()
        durations.append((time.perf_counter() - started) * 1_000)
    result = BenchmarkResult(
        name=name,
        iterations=repetitions,
        p50_ms=round(statistics.median(durations), 3),
        p95_ms=round(_percentile(durations, 0.95), 3),
        max_ms=round(max(durations), 3),
        threshold_ms=threshold_ms,
        passed=_percentile(durations, 0.95) <= threshold_ms,
    )
    print(
        f"{name}: p50={result.p50_ms:.3f}ms, p95={result.p95_ms:.3f}ms, "
        f"threshold={threshold_ms:.0f}ms, {'PASS' if result.passed else 'FAIL'}",
        flush=True,
    )
    return result


async def _measure_async(
    name: str,
    action: Callable[[], Awaitable[object]],
    *,
    repetitions: int,
    threshold_ms: float,
) -> BenchmarkResult:
    await action()
    durations: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        await action()
        durations.append((time.perf_counter() - started) * 1_000)
    result = BenchmarkResult(
        name=name,
        iterations=repetitions,
        p50_ms=round(statistics.median(durations), 3),
        p95_ms=round(_percentile(durations, 0.95), 3),
        max_ms=round(max(durations), 3),
        threshold_ms=threshold_ms,
        passed=_percentile(durations, 0.95) <= threshold_ms,
    )
    print(
        f"{name}: p50={result.p50_ms:.3f}ms, p95={result.p95_ms:.3f}ms, "
        f"threshold={threshold_ms:.0f}ms, {'PASS' if result.passed else 'FAIL'}",
        flush=True,
    )
    return result


def _validate_database(engine: Any) -> str:
    with engine.connect() as connection:
        database_name = str(connection.scalar(text("SELECT current_database()")))
        row_count = int(connection.scalar(text("SELECT count(*) FROM stock_daily")) or 0)
        server_major = int(
            connection.scalar(text("SELECT current_setting('server_version_num')::int / 10000"))
            or 0
        )
    if database_name != "stock_data_sync_perf":
        raise RuntimeError(
            "performance tests only run against the dedicated stock_data_sync_perf database"
        )
    if row_count:
        raise RuntimeError("performance database must be empty")
    if server_major != 18:
        raise RuntimeError("performance tests require PostgreSQL 18")
    return database_name


def _trading_range(engine: Any, profile: ScaleProfile) -> tuple[date, date]:
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    """
                SELECT day::date
                FROM generate_series(
                    DATE '2019-01-01', DATE '2035-12-31', INTERVAL '1 day'
                ) AS day
                WHERE extract(isodow FROM day) <= 5
                ORDER BY day
                LIMIT :trading_day_count
                """
                ),
                {"trading_day_count": profile.trading_day_count},
            )
            .scalars()
            .all()
        )
    if len(rows) != profile.trading_day_count:
        raise RuntimeError("could not construct the requested trading-day range")
    return rows[0], rows[-1]


def _create_partitions(engine: Any, first_day: date, last_day: date) -> int:
    count = 0
    current = month_start(first_day)
    final = month_start(last_day)
    with engine.begin() as connection:
        while current <= final:
            count += len(
                ensure_monthly_partitions(
                    connection,
                    reference_date=current,
                    months_ahead=0,
                )
            )
            current = month_start(current, 1)
    return count


def _seed_stocks(engine: Any, profile: ScaleProfile) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO stock (
                    ts_code, symbol, name, exchange, list_status, list_date, synced_at
                )
                SELECT
                    lpad(i::text, 6, '0') || '.SZ',
                    lpad(i::text, 6, '0'),
                    'PERF-' || i::text,
                    'SZSE',
                    'L',
                    DATE '2000-01-01',
                    now()
                FROM generate_series(1, :stock_count) AS i
                """
            ),
            {"stock_count": profile.stock_count},
        )


def _seed_business_rows(engine: Any, profile: ScaleProfile) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                WITH trading_dates AS (
                    SELECT day::date AS trade_date
                    FROM generate_series(
                        DATE '2019-01-01', DATE '2035-12-31', INTERVAL '1 day'
                    ) AS day
                    WHERE extract(isodow FROM day) <= 5
                    ORDER BY day
                    LIMIT :trading_day_count
                )
                INSERT INTO stock_daily (
                    ts_code, trade_date, open, high, low, close, pre_close,
                    change, pct_chg, volume, amount, after_hours_volume,
                    after_hours_amount, adj_factor, turnover_rate, turnover_rate_f,
                    volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
                    total_share, float_share, free_share, total_mv, circ_mv,
                    limit_status, up_limit, down_limit, synced_at
                )
                SELECT
                    stock.ts_code,
                    trading_dates.trade_date,
                    10.000000,
                    10.500000,
                    9.500000,
                    10.100000,
                    10.000000,
                    0.100000,
                    1.000000,
                    100000.0000,
                    1000000.0000,
                    1000.0000,
                    10000.0000,
                    1.00000000,
                    2.000000,
                    1.500000,
                    1.100000,
                    15.000000,
                    14.000000,
                    1.500000,
                    2.000000,
                    1.900000,
                    1.000000,
                    0.900000,
                    1000000000.0000,
                    800000000.0000,
                    700000000.0000,
                    10100000000.0000,
                    8080000000.0000,
                    0,
                    11.100000,
                    9.090000,
                    now()
                FROM stock
                CROSS JOIN trading_dates
                """
            ),
            {"trading_day_count": profile.trading_day_count},
        )


def _seed_runtime_rows(engine: Any, profile: ScaleProfile) -> None:
    tasks_per_batch = profile.collection_task_count // profile.batch_count
    processes_per_batch = profile.processing_task_count // profile.batch_count
    if tasks_per_batch * profile.batch_count != profile.collection_task_count:
        raise ValueError("collection task count must be divisible by batch count")
    if processes_per_batch * profile.batch_count != profile.processing_task_count:
        raise ValueError("processing task count must be divisible by batch count")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO collection_batch (
                    batch_id, batch_type, business_date, status, scheduled_at,
                    plan_version, expected_task_count, planning_completed_at,
                    started_at, closed_at
                )
                SELECT
                    md5('batch-' || i::text)::uuid,
                    'BACKFILL',
                    current_date - (i % 3650),
                    CASE WHEN i <= :active_batch_count THEN 'PENDING' ELSE 'CLOSED' END,
                    now() - i * INTERVAL '10 minutes',
                    md5('plan-' || i::text),
                    :tasks_per_batch,
                    now() - i * INTERVAL '10 minutes',
                    CASE WHEN i > :active_batch_count
                        THEN now() - i * INTERVAL '10 minutes' END,
                    CASE WHEN i > :active_batch_count
                        THEN now() - i * INTERVAL '10 minutes' + INTERVAL '5 minutes' END
                FROM generate_series(1, :batch_count) AS i
                """
            ),
            {
                "active_batch_count": profile.active_batch_count,
                "batch_count": profile.batch_count,
                "tasks_per_batch": tasks_per_batch,
            },
        )
        connection.execute(
            text(
                """
                WITH generated AS (
                    SELECT
                        i,
                        ((i - 1) / :tasks_per_batch)::integer + 1 AS batch_number
                    FROM generate_series(1, :task_count) AS i
                )
                INSERT INTO collection_task (
                    task_id, batch_id, provider, api_name, scope_key,
                    request_params, status, attempt_count, max_attempts,
                    request_count, row_count, started_at, finished_at,
                    error_code, error_message, warning_message
                )
                SELECT
                    md5('collection-task-' || i::text)::uuid,
                    md5('batch-' || batch_number::text)::uuid,
                    'TUSHARE',
                    'endpoint_' || (i % 20)::text,
                    'scope_' || ((i - 1) % :tasks_per_batch)::text,
                    '{}'::jsonb,
                    CASE
                        WHEN batch_number <= :active_batch_count THEN 'PENDING'
                        WHEN i % 100 = 0 THEN 'FAILED'
                        WHEN i % 30 = 0 THEN 'EMPTY_VALID'
                        ELSE 'SUCCESS'
                    END,
                    CASE WHEN batch_number <= :active_batch_count THEN 0 ELSE 1 END,
                    3,
                    CASE WHEN batch_number <= :active_batch_count THEN 0 ELSE 1 END,
                    CASE WHEN batch_number <= :active_batch_count THEN NULL ELSE 5000 END,
                    CASE WHEN batch_number > :active_batch_count
                        THEN now() - batch_number * INTERVAL '10 minutes' END,
                    CASE WHEN batch_number > :active_batch_count
                        THEN now() - batch_number * INTERVAL '10 minutes'
                            + INTERVAL '4 minutes' END,
                    CASE WHEN i % 100 = 0 THEN 'PERF_FAILURE' END,
                    CASE WHEN i % 100 = 0 THEN 'performance fixture failure' END,
                    CASE WHEN i % 30 = 0 AND i % 100 <> 0
                        THEN 'performance fixture data gap' END
                FROM generated
                """
            ),
            {
                "active_batch_count": profile.active_batch_count,
                "task_count": profile.collection_task_count,
                "tasks_per_batch": tasks_per_batch,
            },
        )
        connection.execute(
            text(
                """
                WITH generated AS (
                    SELECT
                        i,
                        ((i - 1) / :processes_per_batch)::integer + 1 AS batch_number
                    FROM generate_series(1, :process_count) AS i
                )
                INSERT INTO processing_task (
                    process_id, source_batch_id, process_type, business_date,
                    output_dataset, output_version, status, priority,
                    attempt_count, max_attempts, queued_at, started_at,
                    finished_at, rows_read, rows_rejected, rows_written,
                    error_message, warning_message
                )
                SELECT
                    md5('processing-task-' || i::text)::uuid,
                    md5('batch-' || batch_number::text)::uuid,
                    'dataset@1',
                    current_date - (batch_number % 3650),
                    'dataset_' || (i % 10)::text,
                    md5('output-version-' || i::text)::uuid,
                    CASE
                        WHEN i > :queued_start THEN 'QUEUED'
                        WHEN i > :blocked_start THEN 'BLOCKED'
                        WHEN i > :release_count AND i % 100 = 0 THEN 'FAILED'
                        ELSE 'SUCCESS'
                    END,
                    CASE WHEN i % 20 = 0 THEN 50 ELSE 400 END,
                    CASE WHEN i <= :blocked_start THEN 1 ELSE 0 END,
                    3,
                    now() - (i % 86400) * INTERVAL '1 second',
                    CASE WHEN i <= :blocked_start
                        THEN now() - (i % 86400) * INTERVAL '1 second' END,
                    CASE WHEN i <= :blocked_start
                        THEN now() - (i % 86400) * INTERVAL '1 second'
                            + INTERVAL '2 seconds' END,
                    CASE WHEN i <= :blocked_start THEN 15000 END,
                    CASE WHEN i <= :blocked_start THEN 0 END,
                    CASE WHEN i <= :blocked_start THEN 5500 END,
                    CASE
                        WHEN i > :blocked_start AND i <= :queued_start
                            THEN 'performance fixture blocked'
                        WHEN i > :release_count AND i % 100 = 0
                            THEN 'performance fixture failure'
                    END,
                    CASE WHEN i <= :blocked_start
                            AND NOT (i > :release_count AND i % 100 = 0)
                            AND i % 5 = 0
                        THEN 'performance fixture quality warning' END
                FROM generated
                """
            ),
            {
                "blocked_start": profile.processing_task_count * 8 // 10,
                "process_count": profile.processing_task_count,
                "processes_per_batch": processes_per_batch,
                "queued_start": profile.processing_task_count * 9 // 10,
                "release_count": profile.release_count,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO processing_dependency (
                    process_id, dependency_type, dependency_name,
                    dependency_scope_key, dependency_scope, status
                )
                SELECT
                    process_id,
                    'RAW_ASSET',
                    'endpoint_' || dependency_number::text,
                    'ALL',
                    '{"scope":"ALL"}'::jsonb,
                    CASE WHEN status = 'BLOCKED' AND dependency_number = 1
                        THEN 'MISSING' ELSE 'READY' END
                FROM processing_task
                CROSS JOIN generate_series(1, 3) AS dependency_number
                """
            )
        )
        connection.execute(
            text(
                """
                WITH generated AS (
                    SELECT i
                    FROM generate_series(1, :release_count) AS i
                )
                INSERT INTO dataset_release (
                    dataset_name, scope_type, scope_key, business_date,
                    version_id, process_id, row_count, published_at
                )
                SELECT
                    'dataset_' || (i % 10)::text,
                    'DATE',
                    'scope_' || i::text,
                    current_date - (i % 3650),
                    md5('output-version-' || i::text)::uuid,
                    md5('processing-task-' || i::text)::uuid,
                    5500,
                    now() - i * INTERVAL '10 minutes'
                FROM generated
                """
            ),
            {"release_count": profile.release_count},
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_request_log (
                    request_id, task_id, provider, endpoint, requested_at,
                    finished_at, status, duration_ms, rate_limit_wait_ms,
                    row_count, error_code
                )
                SELECT
                    md5('provider-request-' || i::text)::uuid,
                    md5(
                        'collection-task-'
                        || (((i - 1) % :task_count) + 1)::text
                    )::uuid,
                    'tushare',
                    'endpoint_' || (i % 20)::text,
                    CASE WHEN i <= :today_count
                        THEN now() - (i % 3600) * INTERVAL '1 second'
                        ELSE now() - ((i % 3650) + 1) * INTERVAL '1 day' END,
                    CASE WHEN i <= :today_count
                        THEN now() - (i % 3600) * INTERVAL '1 second'
                            + (20 + i % 500) * INTERVAL '1 millisecond'
                        ELSE now() - ((i % 3650) + 1) * INTERVAL '1 day'
                            + (20 + i % 500) * INTERVAL '1 millisecond' END,
                    CASE WHEN i % 100 = 0 THEN 'ERROR' ELSE 'SUCCESS' END,
                    20 + i % 500,
                    CASE WHEN i % 50 = 0 THEN 100 ELSE 0 END,
                    CASE WHEN i % 100 = 0 THEN NULL ELSE 5000 END,
                    CASE WHEN i % 100 = 0 THEN 'PERF_PROVIDER_FAILURE' END
                FROM generate_series(1, :request_count) AS i
                """
            ),
            {
                "request_count": profile.provider_request_count,
                "task_count": profile.collection_task_count,
                "today_count": min(50_000, profile.provider_request_count // 2),
            },
        )


def _analyze(engine: Any) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("VACUUM (ANALYZE)")


def _daily_rows(profile: ScaleProfile, business_date: date) -> tuple[dict[str, object], ...]:
    synced_at = datetime.now(UTC)
    return tuple(
        {
            "ts_code": f"{index:06}.SZ",
            "trade_date": business_date,
            "open": Decimal("10.000000"),
            "high": Decimal("10.500000"),
            "low": Decimal("9.500000"),
            "close": Decimal("10.100000"),
            "pre_close": Decimal("10.000000"),
            "change": Decimal("0.100000"),
            "pct_chg": Decimal("1.000000"),
            "volume": Decimal("100000.0000"),
            "amount": Decimal("1000000.0000"),
            "after_hours_volume": Decimal("1000.0000"),
            "after_hours_amount": Decimal("10000.0000"),
            "adj_factor": Decimal("1.00000000"),
            "turnover_rate": Decimal("2.000000"),
            "turnover_rate_f": Decimal("1.500000"),
            "volume_ratio": Decimal("1.100000"),
            "pe": Decimal("15.000000"),
            "pe_ttm": Decimal("14.000000"),
            "pb": Decimal("1.500000"),
            "ps": Decimal("2.000000"),
            "ps_ttm": Decimal("1.900000"),
            "dv_ratio": Decimal("1.000000"),
            "dv_ttm": Decimal("0.900000"),
            "total_share": Decimal("1000000000.0000"),
            "float_share": Decimal("800000000.0000"),
            "free_share": Decimal("700000000.0000"),
            "total_mv": Decimal("10100000000.0000"),
            "circ_mv": Decimal("8080000000.0000"),
            "limit_status": 0,
            "up_limit": Decimal("11.100000"),
            "down_limit": Decimal("9.090000"),
            "synced_at": synced_at,
        }
        for index in range(1, profile.stock_count + 1)
    )


def _benchmark_staging_publish(
    session_factory: sessionmaker[Session],
    profile: ScaleProfile,
    business_date: date,
) -> BenchmarkResult:
    rows = _daily_rows(profile, business_date)
    publisher = PostgresStagingPublisher()
    update_columns = (
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

    def publish() -> None:
        with session_factory() as session, session.begin():
            written = publisher.publish(
                session,
                target=StockDaily.__table__,
                rows=rows,
                strategy=WriteStrategy.REPLACE_DATE,
                key_columns=("ts_code", "trade_date"),
                update_columns=update_columns,
                replace_filters={"trade_date": business_date},
            )
            if written != profile.stock_count:
                raise RuntimeError("staging publisher wrote an unexpected number of rows")

    return _measure(
        "daily_partition_replace_via_copy",
        publish,
        repetitions=max(3, min(profile.repetitions, 5)),
        threshold_ms=3_000,
    )


def _benchmark_sync_queries(
    engine: Any,
    session_factory: sessionmaker[Session],
    profile: ScaleProfile,
    last_day: date,
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    connection = engine.connect()
    results.append(
        _measure(
            "business_rows_by_trade_date",
            lambda: connection.execute(
                text(
                    """
                    SELECT ts_code, close, volume
                    FROM stock_daily
                    WHERE trade_date = :trade_date
                    ORDER BY ts_code
                    """
                ),
                {"trade_date": last_day},
            ).all(),
            repetitions=profile.repetitions,
            threshold_ms=250,
        )
    )
    results.append(
        _measure(
            "business_rows_by_security",
            lambda: connection.execute(
                text(
                    """
                    SELECT trade_date, close, volume
                    FROM stock_daily
                    WHERE ts_code = '000001.SZ'
                    ORDER BY trade_date
                    """
                )
            ).all(),
            repetitions=profile.repetitions,
            threshold_ms=250,
        )
    )
    connection.close()

    acquisition = AcquisitionRepository(session_factory)
    claimed_collection_ids: list[Any] = []

    def claim_collection() -> object:
        claimed = acquisition.claim_next(
            allowed_batch_types=(BatchType.BACKFILL,),
            now=datetime.now(UTC),
        )
        if claimed is None:
            raise RuntimeError("collection queue unexpectedly became empty")
        claimed_collection_ids.append(claimed.task_id)
        with session_factory() as session, session.begin():
            session.execute(
                update(CollectionTask)
                .where(CollectionTask.task_id == claimed.task_id)
                .values(
                    status=CollectionTaskStatus.SUCCESS.value,
                    finished_at=datetime.now(UTC),
                )
            )
        return claimed

    results.append(
        _measure(
            "collection_queue_claim",
            claim_collection,
            repetitions=profile.repetitions,
            threshold_ms=100,
        )
    )

    processing = ProcessingRepository(session_factory)

    def claim_processing() -> object:
        claimed = processing.claim_next(
            now=datetime.now(UTC),
            advisory_lock_id=987_654_321,
        )
        if claimed is None:
            raise RuntimeError("processing queue unexpectedly became empty")
        with session_factory() as session, session.begin():
            session.execute(
                update(ProcessingTask)
                .where(ProcessingTask.process_id == claimed.process_id)
                .values(status=ProcessingTaskStatus.SUCCESS.value, finished_at=datetime.now(UTC))
            )
        return claimed

    results.append(
        _measure(
            "processing_queue_claim",
            claim_processing,
            repetitions=profile.repetitions,
            threshold_ms=100,
        )
    )
    return results


async def _benchmark_operations(profile: ScaleProfile) -> list[BenchmarkResult]:
    async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    day_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    since = now - timedelta(days=30)
    results: list[BenchmarkResult] = []
    async with factory() as session:
        repository = OperationsRepository(session)
        cases: tuple[tuple[str, Callable[[], Awaitable[object]]], ...] = (
            ("operations_overview", lambda: repository.overview_counts(day_start=day_start)),
            (
                "operations_acquisition_batches",
                lambda: repository.acquisition_batches(
                    since=since,
                    status=None,
                    business_date=None,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_processing_queue",
                lambda: repository.processing_queue(
                    status=None,
                    dataset_name=None,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_dependencies_attention",
                lambda: repository.dependencies(
                    since=since,
                    readiness="attention",
                    query=None,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_releases",
                lambda: repository.releases(dataset_name=None, offset=0, limit=50),
            ),
            (
                "operations_provider_endpoints",
                lambda: repository.provider_endpoints(day_start=day_start),
            ),
            (
                "operations_run_records",
                lambda: repository.run_records(
                    since=since,
                    run_type=None,
                    status=None,
                    batch_id=None,
                    unresolved_only=False,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_unresolved_processing_runs",
                lambda: repository.run_records(
                    since=since,
                    run_type="processing",
                    status="failed",
                    batch_id=None,
                    unresolved_only=True,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_action_alerts",
                lambda: repository.alert_rows(
                    since=since,
                    category="action_required",
                    source=None,
                    offset=0,
                    limit=50,
                ),
            ),
            (
                "operations_all_alerts",
                lambda: repository.alert_rows(
                    since=since,
                    category="all",
                    source=None,
                    offset=0,
                    limit=50,
                ),
            ),
        )
        for name, action in cases:
            results.append(
                await _measure_async(
                    name,
                    action,
                    repetitions=profile.repetitions,
                    threshold_ms=1_000,
                )
            )
    await async_engine.dispose()
    return results


def _partition_relations(engine: Any, trade_date: date) -> list[str]:
    with engine.connect() as connection:
        raw_plan = connection.execute(
            text(
                """
                EXPLAIN (ANALYZE, FORMAT JSON)
                SELECT ts_code, close
                FROM stock_daily
                WHERE trade_date = :trade_date
                """
            ),
            {"trade_date": trade_date},
        ).scalar_one()
    plan = raw_plan[0]["Plan"]
    relations: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        relation = node.get("Relation Name")
        if isinstance(relation, str) and relation.startswith("stock_daily_p"):
            relations.append(relation)
        for child in node.get("Plans", []):
            visit(child)

    visit(plan)
    return sorted(set(relations))


def _database_stats(engine: Any) -> dict[str, int]:
    queries = {
        "databaseBytes": "SELECT pg_database_size(current_database())",
        "businessRows": "SELECT count(*) FROM stock_daily",
        "collectionBatches": "SELECT count(*) FROM collection_batch",
        "collectionTasks": "SELECT count(*) FROM collection_task",
        "processingTasks": "SELECT count(*) FROM processing_task",
        "processingDependencies": "SELECT count(*) FROM processing_dependency",
        "providerRequests": "SELECT count(*) FROM provider_request_log",
        "datasetReleases": "SELECT count(*) FROM dataset_release",
    }
    with engine.connect() as connection:
        return {
            name: int(connection.scalar(text(statement)) or 0)
            for name, statement in queries.items()
        }


def run(profile: ScaleProfile, json_output: Path) -> int:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    database_name = _validate_database(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    first_day, last_day = _trading_range(engine, profile)
    seed_seconds: dict[str, float] = {}

    (_, seed_seconds["partitions"]) = _run_step(
        "create partitions",
        lambda: _create_partitions(engine, first_day, last_day),
    )
    (_, seed_seconds["stocks"]) = _run_step(
        "seed stocks",
        lambda: _seed_stocks(engine, profile),
    )
    (_, seed_seconds["businessRows"]) = _run_step(
        "seed stock_daily",
        lambda: _seed_business_rows(engine, profile),
    )
    (_, seed_seconds["runtimeRows"]) = _run_step(
        "seed runtime tables",
        lambda: _seed_runtime_rows(engine, profile),
    )
    (_, seed_seconds["vacuumAnalyze"]) = _run_step(
        "vacuum analyze",
        lambda: _analyze(engine),
    )

    results = [
        _benchmark_staging_publish(session_factory, profile, last_day),
        *_benchmark_sync_queries(engine, session_factory, profile, last_day),
        *asyncio.run(_benchmark_operations(profile)),
    ]
    relations = _partition_relations(engine, last_day)
    expected_partition = f"stock_daily_p{last_day:%Y%m}"
    partition_pruning_passed = relations == [expected_partition]
    print(
        f"partition pruning: {relations}, {'PASS' if partition_pruning_passed else 'FAIL'}",
        flush=True,
    )
    counts = _database_stats(engine)
    expected_counts = {
        "businessRows": profile.business_row_count,
        "collectionBatches": profile.batch_count,
        "collectionTasks": profile.collection_task_count,
        "processingTasks": profile.processing_task_count,
        "processingDependencies": profile.processing_task_count * 3,
        "providerRequests": profile.provider_request_count,
        "datasetReleases": profile.release_count,
    }
    counts_passed = all(counts[name] == expected for name, expected in expected_counts.items())
    passed = all(result.passed for result in results) and partition_pruning_passed and counts_passed
    with engine.connect() as connection:
        postgresql_version = str(connection.scalar(text("SHOW server_version")))
    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "passed": passed,
        "database": database_name,
        "profile": asdict(profile),
        "environment": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "postgresql": postgresql_version,
        },
        "tradingDateRange": {"first": first_day.isoformat(), "last": last_day.isoformat()},
        "seedSeconds": {name: round(value, 3) for name, value in seed_seconds.items()},
        "counts": counts,
        "expectedCounts": expected_counts,
        "countsPassed": counts_passed,
        "partitionPruning": {
            "relations": relations,
            "expected": expected_partition,
            "passed": partition_pruning_passed,
        },
        "benchmarks": [asdict(result) for result in results],
    }
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    engine.dispose()
    print(f"overall: {'PASS' if passed else 'FAIL'}", flush=True)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL target-scale performance tests")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="target")
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()
    return run(PROFILES[args.profile], args.json_output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

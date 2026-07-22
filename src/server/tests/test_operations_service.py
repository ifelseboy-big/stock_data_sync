from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.catalog.datasets import ALL_DATASET_SPECS
from app.catalog.specs import ReleaseScope
from app.catalog.tushare import build_tushare_api_registry
from app.modules.acquisition.models import BatchStatus
from app.modules.operations.service import OperationsService, _batch_status


class ProviderRepositoryStub:
    async def provider_endpoints(self, *, day_start: datetime) -> list[dict[str, Any]]:
        del day_start
        return [
            {
                "endpoint": "fund_daily",
                "request_count": 2,
                "success_count": 2,
                "p50": 100.0,
                "p95": 150.0,
                "throttled_count": 1,
                "empty_count": 0,
                "last_requested_at": datetime.now(UTC),
            }
        ]

    async def quota_counts(self, *, window_start: datetime) -> dict[str, int]:
        del window_start
        return {"used": 2, "delayed": 1}


@pytest.mark.asyncio
async def test_provider_monitoring_lists_unrequested_configured_endpoints() -> None:
    service = OperationsService(ProviderRepositoryStub())  # type: ignore[arg-type]

    monitoring = await service.provider_monitoring()

    endpoints = {item.endpoint: item for item in monitoring.endpoints}
    assert set(endpoints) == {spec.api_name for spec in build_tushare_api_registry().all()}
    assert endpoints["fund_daily"].request_count_today == 2
    assert endpoints["fund_daily"].success_rate_today == 1.0
    assert endpoints["fund_daily"].endpoint_display_name == "ETF 日线行情"
    assert endpoints["fund_daily"].endpoint_description
    assert endpoints["daily"].request_count_today == 0
    assert endpoints["daily"].success_rate_today is None


@pytest.mark.asyncio
async def test_releases_include_dataset_presentation() -> None:
    class ReleaseRepositoryStub:
        async def releases(self, **_: Any) -> tuple[list[dict[str, Any]], int]:
            return (
                [
                    {
                        "dataset_name": "stock_daily.core",
                        "scope_type": "DATE",
                        "scope_key": "2026-07-21",
                        "business_date": date(2026, 7, 21),
                        "version_id": "version-id",
                        "process_id": "process-id",
                        "process_type": "stock_daily.core@1",
                        "row_count": 100,
                        "published_at": datetime.now(UTC),
                    }
                ],
                1,
            )

    service = OperationsService(ReleaseRepositoryStub())  # type: ignore[arg-type]

    releases = await service.releases(dataset_name=None, page=1, page_size=20)

    assert releases.items[0].dataset_display_name == "股票核心日线"
    assert releases.items[0].dataset_name == "stock_daily.core"
    assert releases.items[0].dataset_description


def test_closed_batch_is_presented_as_a_result_status() -> None:
    assert _batch_status(BatchStatus.CLOSED.value, failed_count=0) == "succeeded"
    assert _batch_status(BatchStatus.CLOSED.value, failed_count=1) == "partial_failed"
    assert _batch_status(BatchStatus.CANCELLED.value, failed_count=0) == "failed"


def test_processing_data_quality_warning_is_presented_as_warning() -> None:
    now = datetime.now(UTC)

    alert = OperationsService._alert_item(
        {
            "id": "process-id",
            "source": "processing",
            "category": "quality",
            "task_name": "stock_top_list_daily",
            "status": "SUCCESS",
            "error_code": "DATA_QUALITY_WARNING",
            "error_message": "已保留字段更完整的重复记录",
            "occurred_at": now,
        },
        now,
    )

    assert alert.level == "warning"
    assert alert.category == "quality"
    assert alert.task_display_name == "龙虎榜股票明细"
    assert alert.task_name == "stock_top_list_daily"
    assert alert.title == "数据质量提醒"
    assert alert.detail == "已保留字段更完整的重复记录"


def test_collection_data_gap_is_presented_as_warning() -> None:
    now = datetime.now(UTC)

    alert = OperationsService._alert_item(
        {
            "id": "task-id",
            "source": "acquisition",
            "category": "data_gap",
            "task_name": "ths_hot",
            "status": "EMPTY_VALID",
            "error_code": "DATA_GAP_WARNING",
            "error_message": "历史接口未返回数据，已记录数据缺口并停止重试",
            "occurred_at": now,
        },
        now,
    )

    assert alert.level == "warning"
    assert alert.category == "data_gap"
    assert alert.task_display_name == "同花顺股票热榜"
    assert alert.task_name == "ths_hot"
    assert alert.title == "数据缺口提醒"
    assert alert.detail == "历史接口未返回数据，已记录数据缺口并停止重试"


@pytest.mark.asyncio
async def test_release_coverage_distinguishes_missing_and_in_progress_dates() -> None:
    expected = {
        spec.dataset_name for spec in ALL_DATASET_SPECS if spec.release_scope == ReleaseScope.DATE
    }
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    class CoverageRepositoryStub:
        async def release_coverage(self, **_: Any) -> list[tuple[date, set[str]]]:
            return [
                (today, set()),
                (today - timedelta(days=1), set()),
                (today - timedelta(days=2), expected),
            ]

    service = OperationsService(CoverageRepositoryStub())  # type: ignore[arg-type]

    coverage = await service.release_coverage(
        start_date=today - timedelta(days=2),
        end_date=today,
        day_count=None,
    )

    assert [item.coverage_status for item in coverage] == ["pending", "missing", "complete"]
    assert coverage[0].missing_datasets
    assert coverage[0].missing_dataset_display_names
    assert coverage[2].missing_datasets == []

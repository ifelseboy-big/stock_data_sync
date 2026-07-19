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
    assert endpoints["daily"].request_count_today == 0
    assert endpoints["daily"].success_rate_today is None


def test_closed_batch_is_presented_as_a_result_status() -> None:
    assert _batch_status(BatchStatus.CLOSED.value, failed_count=0) == "succeeded"
    assert _batch_status(BatchStatus.CLOSED.value, failed_count=1) == "partial_failed"
    assert _batch_status(BatchStatus.CANCELLED.value, failed_count=0) == "failed"


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

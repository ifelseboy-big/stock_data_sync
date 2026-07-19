from datetime import UTC, datetime
from typing import Any

import pytest

from app.catalog.tushare import build_tushare_api_registry
from app.modules.operations.service import OperationsService


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

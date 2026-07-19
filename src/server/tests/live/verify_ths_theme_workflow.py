from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.models import CollectionTask
from app.modules.operations.models import ProviderRequestLog
from app.modules.topics.models import (
    MarketThemeDaily,
    MarketThemeMemberDaily,
    ThemeIndex,
    ThemeIndexDaily,
    ThemeIndexMember,
)
from tests.live.verify_recent_workflows import (
    OperationsApi,
    _assert_collection_success,
    _batch_ids_from_command,
    _close_batches,
    _drain_collection,
    _plan_and_drain_processing,
    _validate_environment,
)


def _repair(api: OperationsApi, business_date: date, api_name: str) -> UUID:
    result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": business_date.isoformat(),
            "apiNames": [api_name],
            "reason": f"同花顺主题指数单链路验证：{api_name}",
        },
        f"live-ths-theme-{api_name}-{uuid4()}",
    )
    batch_id = _batch_ids_from_command(result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((batch_id,), api_name)
    _plan_and_drain_processing((batch_id,), api_name)
    return batch_id


def _count(model: Any) -> int:
    with SyncSessionFactory() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _verify(business_date: date, batch_ids: tuple[UUID, ...]) -> dict[str, object]:
    with SyncSessionFactory() as session:
        themes = list(session.scalars(select(ThemeIndex).order_by(ThemeIndex.ts_code)))
        daily_count = int(
            session.scalar(
                select(func.count())
                .select_from(ThemeIndexDaily)
                .where(ThemeIndexDaily.trade_date == business_date)
            )
            or 0
        )
        member_count = int(session.scalar(select(func.count()).select_from(ThemeIndexMember)) or 0)
        catl_membership = int(
            session.scalar(
                select(func.count())
                .select_from(ThemeIndexMember)
                .where(
                    ThemeIndexMember.ts_code == "700056.TI",
                    ThemeIndexMember.con_code == "300750.SZ",
                    ThemeIndexMember.is_current.is_(True),
                )
            )
            or 0
        )
        request_rows = list(
            session.execute(
                select(
                    ProviderRequestLog.endpoint,
                    func.count(),
                    func.count().filter(ProviderRequestLog.status == "SUCCESS"),
                    func.coalesce(func.sum(ProviderRequestLog.row_count), 0),
                )
                .join(CollectionTask, CollectionTask.task_id == ProviderRequestLog.task_id)
                .where(CollectionTask.batch_id.in_(batch_ids))
                .group_by(ProviderRequestLog.endpoint)
                .order_by(ProviderRequestLog.endpoint)
            )
        )
    if not themes or any(item.theme_type != "TH" or item.source != "THS" for item in themes):
        raise RuntimeError("theme_index did not publish a valid THS type=TH master")
    if daily_count == 0:
        raise RuntimeError(f"theme_index_daily is empty for {business_date}")
    if member_count == 0 or catl_membership != 1:
        raise RuntimeError("theme_index_member is incomplete")
    return {
        "businessDate": business_date.isoformat(),
        "themeIndexCount": len(themes),
        "themeNames": [item.name for item in themes],
        "themeDailyCount": daily_count,
        "themeMemberCount": member_count,
        "catlInNingPortfolio": catl_membership == 1,
        "providerRequests": {
            endpoint: {
                "requestCount": int(request_count),
                "successCount": int(success_count),
                "rowCount": int(row_count),
            }
            for endpoint, request_count, success_count, row_count in request_rows
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="验证同花顺主题指数单链路")
    parser.add_argument("--month-start", type=date.fromisoformat, required=True)
    parser.add_argument("--business-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    _validate_environment()
    api = OperationsApi()
    before_dc = (_count(MarketThemeDaily), _count(MarketThemeMemberDaily))
    try:
        master_batch = _repair(api, args.month_start, "ths_index")
        member_batch = _repair(api, args.month_start, "ths_member")
        daily_batch = _repair(api, args.business_date, "ths_daily")
        result = _verify(args.business_date, (master_batch, member_batch, daily_batch))
    finally:
        api.close()
    after_dc = (_count(MarketThemeDaily), _count(MarketThemeMemberDaily))
    if after_dc != before_dc:
        raise RuntimeError(
            f"DC topic data changed unexpectedly: before={before_dc}, after={after_dc}"
        )
    result["dcTopicRowsUnchanged"] = {
        "marketThemeDaily": after_dc[0],
        "marketThemeMemberDaily": after_dc[1],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

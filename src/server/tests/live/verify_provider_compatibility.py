from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.models import CollectionTask, CollectionTaskStatus
from app.modules.processing.models import ProcessingTask, ProcessingTaskStatus
from app.modules.stocks.models import ThsBoardMoneyflowDaily
from app.modules.topics.models import (
    MarketThemeDaily,
    MarketThemeMemberDaily,
    StockHotRankDaily,
)
from tests.live.verify_recent_workflows import (
    LiveWorkflowError,
    OperationsApi,
    _assert_collection_success,
    _batch_ids_from_command,
    _close_batches,
    _drain_collection,
    _plan_and_drain_processing,
    _validate_environment,
)


def _repair(
    api: OperationsApi,
    *,
    business_date: date,
    api_names: list[str],
    label: str,
) -> tuple[UUID, ...]:
    result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": business_date.isoformat(),
            "apiNames": api_names,
            "reason": f"供应商兼容专项验收：{label}",
        },
        f"live-provider-compatibility-{business_date:%Y%m%d}-{'-'.join(api_names)}-v1",
    )
    batch_ids = _batch_ids_from_command(result)
    _drain_collection()
    _close_batches()
    _assert_collection_success(batch_ids, label)
    _plan_and_drain_processing(batch_ids, label)
    return batch_ids


def _verify_hot_rank(api: OperationsApi) -> dict[str, Any]:
    business_date = date(2026, 5, 8)
    batch_ids = _repair(
        api,
        business_date=business_date,
        api_names=["dc_hot"],
        label="dc_hot multi-snapshot",
    )
    with SyncSessionFactory() as session:
        raw_rows = int(
            session.scalar(
                select(func.coalesce(func.sum(CollectionTask.row_count), 0)).where(
                    CollectionTask.batch_id.in_(batch_ids),
                    CollectionTask.api_name == "dc_hot",
                )
            )
            or 0
        )
        rows = tuple(
            session.scalars(
                select(StockHotRankDaily).where(
                    StockHotRankDaily.trade_date == business_date,
                    StockHotRankDaily.source == "DC",
                )
            )
        )
    if not rows or raw_rows <= len(rows):
        raise LiveWorkflowError(
            f"dc_hot did not reduce multiple snapshots: raw={raw_rows}, final={len(rows)}"
        )
    stock_keys = {(row.market_type, row.rank_type, row.ts_code) for row in rows}
    rank_keys = {(row.market_type, row.rank_type, row.rank) for row in rows}
    snapshots: dict[tuple[str, str], set[datetime]] = {}
    for row in rows:
        snapshots.setdefault((row.market_type, row.rank_type), set()).add(row.rank_time)
    if len(stock_keys) != len(rows) or len(rank_keys) != len(rows):
        raise LiveWorkflowError("dc_hot final snapshot still contains duplicate stock or rank")
    if any(len(values) != 1 for values in snapshots.values()):
        raise LiveWorkflowError("dc_hot final data contains more than one snapshot per scope")
    return {"batchIds": [str(value) for value in batch_ids], "rawRows": raw_rows, "rows": len(rows)}


def _verify_ths_hot_history(api: OperationsApi) -> dict[str, Any]:
    business_date = date(2026, 1, 9)
    batch_ids = _repair(
        api,
        business_date=business_date,
        api_names=["ths_hot", "dc_hot"],
        label="ths_hot historical snapshots",
    )
    with SyncSessionFactory() as session:
        raw_rows = int(
            session.scalar(
                select(func.coalesce(func.sum(CollectionTask.row_count), 0)).where(
                    CollectionTask.batch_id.in_(batch_ids),
                    CollectionTask.api_name == "ths_hot",
                )
            )
            or 0
        )
        rows = tuple(
            session.scalars(
                select(StockHotRankDaily).where(
                    StockHotRankDaily.trade_date == business_date,
                    StockHotRankDaily.source == "THS",
                )
            )
        )
    stock_keys = {row.ts_code for row in rows}
    rank_keys = {row.rank for row in rows}
    snapshot_minutes = {row.rank_time.replace(second=0, microsecond=0) for row in rows}
    if not rows or raw_rows <= len(rows):
        raise LiveWorkflowError(
            f"ths_hot did not reduce historical snapshots: raw={raw_rows}, final={len(rows)}"
        )
    if len(stock_keys) != len(rows) or len(rank_keys) != len(rows):
        raise LiveWorkflowError("ths_hot final snapshot contains duplicate stock or rank")
    if len(snapshot_minutes) != 1:
        raise LiveWorkflowError("ths_hot final data contains more than one minute snapshot")
    return {"batchIds": [str(value) for value in batch_ids], "rawRows": raw_rows, "rows": len(rows)}


def _verify_board_moneyflow(api: OperationsApi) -> dict[str, Any]:
    business_date = date(2026, 5, 20)
    batch_ids = _repair(
        api,
        business_date=business_date,
        api_names=["moneyflow_cnt_ths", "moneyflow_ind_ths"],
        label="THS board moneyflow nullable code",
    )
    with SyncSessionFactory() as session:
        rows = tuple(
            session.scalars(
                select(ThsBoardMoneyflowDaily).where(
                    ThsBoardMoneyflowDaily.trade_date == business_date
                )
            )
        )
    nullable_names = sorted(row.board_name for row in rows if row.ts_code is None)
    if not rows or "AI视频" not in nullable_names:
        raise LiveWorkflowError(
            f"THS board moneyflow nullable-code row is missing: {nullable_names}"
        )
    return {
        "batchIds": [str(value) for value in batch_ids],
        "rows": len(rows),
        "nullableCodeBoards": nullable_names,
    }


def _verify_theme_retention(api: OperationsApi) -> dict[str, Any]:
    business_date = date(2026, 2, 3)
    batch_ids = _repair(
        api,
        business_date=business_date,
        api_names=["dc_concept", "dc_concept_cons"],
        label="DC theme historical retention",
    )
    with SyncSessionFactory() as session:
        collection = {
            row.api_name: (row.status, row.row_count)
            for row in session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id.in_(batch_ids))
            )
        }
        processing_statuses = tuple(
            session.scalars(
                select(ProcessingTask.status).where(ProcessingTask.source_batch_id.in_(batch_ids))
            )
        )
        theme_count = int(
            session.scalar(
                select(func.count())
                .select_from(MarketThemeDaily)
                .where(MarketThemeDaily.trade_date == business_date)
            )
            or 0
        )
        member_count = int(
            session.scalar(
                select(func.count())
                .select_from(MarketThemeMemberDaily)
                .where(MarketThemeMemberDaily.trade_date == business_date)
            )
            or 0
        )
    main_status, main_rows = collection.get("dc_concept", (None, None))
    member_status, member_rows = collection.get("dc_concept_cons", (None, None))
    if main_status != CollectionTaskStatus.EMPTY_VALID.value or main_rows != 0:
        raise LiveWorkflowError(f"dc_concept historical empty result is invalid: {collection}")
    if member_status != CollectionTaskStatus.SUCCESS.value or not member_rows:
        raise LiveWorkflowError(f"dc_concept_cons historical rows are invalid: {collection}")
    if not processing_statuses or any(
        status != ProcessingTaskStatus.SUCCESS.value for status in processing_statuses
    ):
        raise LiveWorkflowError(f"theme processing did not succeed: {processing_statuses}")
    if theme_count != 0 or member_count <= 0:
        raise LiveWorkflowError(
            f"historical theme retention mismatch: themes={theme_count}, members={member_count}"
        )
    return {
        "batchIds": [str(value) for value in batch_ids],
        "themeRows": theme_count,
        "memberRows": member_count,
    }


def run(report_path: Path) -> dict[str, Any]:
    database = _validate_environment()
    api = OperationsApi()
    try:
        report = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "database": database,
            "dcHot": _verify_hot_rank(api),
            "thsHotHistory": _verify_ths_hot_history(api),
            "boardMoneyflow": _verify_board_moneyflow(api),
            "themeRetention": _verify_theme_retention(api),
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"provider compatibility report written: {report_path}", flush=True)
        return report
    finally:
        api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.report.resolve())


if __name__ == "__main__":
    main()

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.catalog import ApiSpec
from app.catalog.specs import ParameterValue
from app.common.errors import CalendarCoverageError
from app.modules.acquisition.domain import BatchPlanResult, TaskBlueprint
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.repository import AcquisitionRepository

TRADING_DAY_BATCH_TYPES = frozenset({BatchType.DAILY, BatchType.HOT})


@dataclass(frozen=True, slots=True)
class StagePlan:
    batch_type: BatchType
    business_date: date | None
    scheduled_at: datetime
    api_specs: tuple[ApiSpec, ...]
    finalize: bool


@dataclass(frozen=True, slots=True)
class StagePlanResult:
    batch_id: UUID | None
    skipped_closed_day: bool
    plan: BatchPlanResult | None


class CollectionPlanner:
    def __init__(self, repository: AcquisitionRepository) -> None:
        self._repository = repository

    def plan(self, stage: StagePlan, *, now: datetime) -> StagePlanResult:
        if stage.batch_type in TRADING_DAY_BATCH_TYPES:
            if stage.business_date is None:
                raise ValueError("daily and hot batches require a business date")
            is_open = self._repository.is_trading_day(stage.business_date)
            if is_open is None:
                raise CalendarCoverageError(
                    f"trade calendar has no SSE row for {stage.business_date.isoformat()}"
                )
            if not is_open:
                return StagePlanResult(batch_id=None, skipped_closed_day=True, plan=None)

        batch_id = self._repository.create_or_get_batch(
            batch_type=stage.batch_type,
            business_date=stage.business_date,
            scheduled_at=stage.scheduled_at,
        )
        blueprints = tuple(
            TaskBlueprint(
                provider=spec.provider,
                api_name=spec.api_name,
                scope_key=scope.scope_key,
                request_params=_json_params(scope.params),
                max_attempts=spec.retry_policy.max_attempts,
            )
            for spec in stage.api_specs
            for scope in spec.scope_builder(stage.business_date)
        )
        plan = self._repository.append_tasks(
            batch_id,
            blueprints,
            finalize=stage.finalize,
            now=now,
        )
        return StagePlanResult(batch_id=batch_id, skipped_closed_day=False, plan=plan)


def _json_params(params: Mapping[str, ParameterValue]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, date):
            result[str(key)] = value.strftime("%Y%m%d")
        elif isinstance(value, tuple):
            result[str(key)] = list(value)
        else:
            result[str(key)] = value
    return result

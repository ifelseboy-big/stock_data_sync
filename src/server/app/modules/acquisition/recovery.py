from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from app.catalog import ApiSpec, SpecRegistry
from app.modules.acquisition.domain import RunningTaskSnapshot
from app.modules.acquisition.repository import AcquisitionRepository
from app.storage import LocalRawAssetStore, RawAssetContext, RawAssetMetadata, schema_fingerprint


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    completed_tasks: int
    retried_tasks: int
    missing_assets: int
    removed_temporary_files: int


class AcquisitionRecovery:
    def __init__(
        self,
        *,
        repository: AcquisitionRepository,
        asset_store: LocalRawAssetStore,
        api_specs: SpecRegistry[ApiSpec],
        timezone: ZoneInfo,
        running_timeout_seconds: int,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._api_specs = api_specs
        self._timezone = timezone
        self._running_timeout = timedelta(seconds=running_timeout_seconds)

    def reconcile(
        self,
        *,
        recover_all_running: bool = False,
        audit_all_assets: bool = True,
    ) -> RecoveryReport:
        now = datetime.now(self._timezone)
        completed_tasks = 0
        retried_tasks = 0
        missing_assets = 0

        running_tasks = self._repository.running_tasks()
        assets = (
            self._repository.assets()
            if audit_all_assets
            else self._repository.assets_for_tasks(tuple(task.task_id for task in running_tasks))
        )
        assets_by_task = {item.task_id: item for item in assets}
        for asset in assets:
            if not self._asset_store.exists(asset.storage_uri):
                self._repository.mark_asset_missing(asset.task_id, now=now)
                missing_assets += 1

        for task in running_tasks:
            is_stale = (
                task.started_at is None
                or now - task.started_at >= self._running_timeout
                or recover_all_running
            )
            persisted = assets_by_task.get(task.task_id)
            if persisted is not None:
                if self._asset_store.exists(persisted.storage_uri):
                    metadata = RawAssetMetadata(
                        storage_uri=persisted.storage_uri,
                        content_hash=persisted.content_hash,
                        schema_fingerprint=persisted.schema_fingerprint,
                        row_count=persisted.row_count,
                        size_bytes=0,
                    )
                    self._asset_store.verify(metadata)
                    self._repository.complete_task(
                        task,
                        metadata,
                        request_count=0,
                        empty=metadata.row_count == 0,
                        completed_at=now,
                    )
                    completed_tasks += 1
                continue

            context = _raw_asset_context(task)
            expected_uri = self._asset_store.expected_uri(context)
            if self._asset_store.exists(expected_uri):
                metadata = self._asset_store.inspect(expected_uri)
                spec = self._api_specs.get(task.api_name)
                if metadata.schema_fingerprint != schema_fingerprint(spec.schema):
                    self._repository.fail_task(
                        task,
                        error_code="SCHEMA_CHANGED",
                        error_message="orphan raw asset schema does not match ApiSpec",
                        request_count=0,
                        retry_at=None,
                        completed_at=now,
                    )
                else:
                    self._repository.complete_task(
                        task,
                        metadata,
                        request_count=0,
                        empty=metadata.row_count == 0,
                        completed_at=now,
                    )
                    completed_tasks += 1
                continue

            if is_stale:
                transition = self._repository.recover_interrupted_task(
                    task,
                    now=now,
                )
                retried_tasks += int(transition.next_retry_at is not None)

        removed_temporary_files = 0
        unregistered_asset_count = 0
        if audit_all_assets:
            orphan_report = self._asset_store.find_orphans(
                tuple(item.storage_uri for item in assets)
            )
            unregistered_asset_count = len(orphan_report.unregistered_assets)
            for path in orphan_report.temporary_files:
                if recover_all_running or _is_stale_path(path, now, self._running_timeout):
                    self._asset_store.remove_temporary_file(path)
                    removed_temporary_files += 1

        structlog.get_logger("acquisition_recovery").info(
            "acquisition_reconciled",
            audit_all_assets=audit_all_assets,
            completed_tasks=completed_tasks,
            retried_tasks=retried_tasks,
            missing_assets=missing_assets,
            removed_temporary_files=removed_temporary_files,
            unregistered_assets=unregistered_asset_count,
        )
        return RecoveryReport(
            completed_tasks=completed_tasks,
            retried_tasks=retried_tasks,
            missing_assets=missing_assets,
            removed_temporary_files=removed_temporary_files,
        )


def _raw_asset_context(task: RunningTaskSnapshot) -> RawAssetContext:
    return RawAssetContext(
        provider=task.provider,
        api_name=task.api_name,
        business_date=task.business_date,
        batch_id=task.batch_id,
        task_id=task.task_id,
        execution_token=task.execution_token,
    )


def _is_stale_path(path: Path, now: datetime, timeout: timedelta) -> bool:
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    return now - modified_at >= timeout

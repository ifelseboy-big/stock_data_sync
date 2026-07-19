from functools import lru_cache
from zoneinfo import ZoneInfo

from app.catalog.specs import ApiSpec, SpecRegistry
from app.catalog.tushare import build_tushare_api_registry
from app.core.config import settings
from app.db.sync_session import SyncSessionFactory
from app.integrations.market_data.tushare_provider import TushareProvider
from app.modules.acquisition.capacity import RawStorageCapacityGate
from app.modules.acquisition.executor import CollectionExecutor
from app.modules.acquisition.planner import CollectionPlanner
from app.modules.acquisition.recovery import AcquisitionRecovery
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.acquisition.runtime import AcquisitionRuntime
from app.observability.provider_calls import PostgresProviderCallRecorder
from app.storage import LocalRawAssetStore


@lru_cache
def get_acquisition_repository() -> AcquisitionRepository:
    return AcquisitionRepository(SyncSessionFactory)


@lru_cache
def get_raw_asset_store() -> LocalRawAssetStore:
    return LocalRawAssetStore(settings.raw_data_dir)


@lru_cache
def get_api_specs() -> SpecRegistry[ApiSpec]:
    return build_tushare_api_registry()


@lru_cache
def get_collection_planner() -> CollectionPlanner:
    return CollectionPlanner(get_acquisition_repository())


@lru_cache
def get_acquisition_recovery() -> AcquisitionRecovery:
    return AcquisitionRecovery(
        repository=get_acquisition_repository(),
        asset_store=get_raw_asset_store(),
        api_specs=get_api_specs(),
        timezone=ZoneInfo(settings.scheduler_timezone),
        running_timeout_seconds=settings.collection_running_timeout_seconds,
    )


@lru_cache
def get_acquisition_runtime() -> AcquisitionRuntime:
    repository = get_acquisition_repository()
    timezone = ZoneInfo(settings.scheduler_timezone)
    executor = CollectionExecutor(
        repository=repository,
        provider=TushareProvider(call_recorder=PostgresProviderCallRecorder(SyncSessionFactory)),
        api_specs=get_api_specs(),
        asset_store=get_raw_asset_store(),
        timezone=timezone,
    )
    return AcquisitionRuntime(
        repository=repository,
        executor=executor,
        capacity_gate=RawStorageCapacityGate(settings.raw_data_dir, settings),
        max_workers=settings.collection_max_workers,
        timezone=timezone,
    )


def shutdown_acquisition_runtime() -> None:
    if get_acquisition_runtime.cache_info().currsize:
        get_acquisition_runtime().shutdown()
        get_acquisition_runtime.cache_clear()

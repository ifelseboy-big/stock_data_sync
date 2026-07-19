from functools import lru_cache
from zoneinfo import ZoneInfo

from app.catalog.datasets import build_dataset_registry
from app.catalog.specs import DatasetSpec, SpecRegistry
from app.core.config import settings
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.factory import get_raw_asset_store
from app.modules.processing.executor import ProcessingExecutor
from app.modules.processing.processors import (
    ConceptBoardDailyProcessor,
    ConceptBoardMemberProcessor,
    ConceptBoardProcessor,
    DatasetProcessor,
    EtfDailyProcessor,
    EtfProcessor,
    EtfShareSizeDailyProcessor,
    IndexDailyBasicProcessor,
    MarketIndexDailyProcessor,
    MarketIndexProcessor,
    MarketIndexWeightProcessor,
    MarketThemeDailyProcessor,
    MarketThemeMemberDailyProcessor,
    StockCompanyProcessor,
    StockDailyCoreProcessor,
    StockDailyLimitProcessor,
    StockHotRankDailyProcessor,
    StockLimitEventDailyProcessor,
    StockLimitStepDailyProcessor,
    StockMoneyflowDailyProcessor,
    StockProcessor,
    StockSuspendDailyProcessor,
    StockTechnicalDailyProcessor,
    StockTopInstDailyProcessor,
    StockTopListDailyProcessor,
    ThemeIndexDailyProcessor,
    ThemeIndexMemberProcessor,
    ThemeIndexProcessor,
    ThsBoardMoneyflowDailyProcessor,
    TradeCalendarProcessor,
)
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.runtime import ProcessingRuntime


@lru_cache
def get_dataset_specs() -> SpecRegistry[DatasetSpec]:
    return build_dataset_registry()


@lru_cache
def get_processing_repository() -> ProcessingRepository:
    return ProcessingRepository(SyncSessionFactory)


@lru_cache
def get_processors() -> dict[str, DatasetProcessor]:
    processors: tuple[DatasetProcessor, ...] = (
        TradeCalendarProcessor(),
        StockProcessor(),
        StockCompanyProcessor(),
        StockDailyCoreProcessor(),
        StockDailyLimitProcessor(),
        StockTechnicalDailyProcessor(),
        StockMoneyflowDailyProcessor(),
        StockSuspendDailyProcessor(),
        EtfProcessor(),
        EtfDailyProcessor(),
        EtfShareSizeDailyProcessor(),
        ConceptBoardProcessor(),
        ConceptBoardDailyProcessor(),
        ConceptBoardMemberProcessor(),
        ThemeIndexProcessor(),
        ThemeIndexDailyProcessor(),
        ThemeIndexMemberProcessor(),
        StockHotRankDailyProcessor(),
        MarketThemeDailyProcessor(),
        MarketThemeMemberDailyProcessor(),
        StockTopListDailyProcessor(),
        StockTopInstDailyProcessor(),
        StockLimitEventDailyProcessor(),
        StockLimitStepDailyProcessor(),
        ThsBoardMoneyflowDailyProcessor(),
        MarketIndexProcessor(),
        MarketIndexDailyProcessor(),
        IndexDailyBasicProcessor(),
        MarketIndexWeightProcessor(),
    )
    return {processor.name: processor for processor in processors}


@lru_cache
def get_processing_runtime() -> ProcessingRuntime:
    repository = get_processing_repository()
    executor = ProcessingExecutor(
        repository=repository,
        dataset_specs=get_dataset_specs(),
        processors=get_processors(),
        asset_store=get_raw_asset_store(),
        timezone=ZoneInfo(settings.scheduler_timezone),
    )
    return ProcessingRuntime(
        repository=repository,
        executor=executor,
        advisory_lock_id=settings.processing_advisory_lock_id,
    )

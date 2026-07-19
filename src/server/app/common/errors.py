class AppError(Exception):
    """Base exception for expected application failures."""

    code = "application_error"


class ConfigurationError(AppError):
    """Raised when a required runtime setting is missing or invalid."""

    code = "configuration_error"


class ProviderError(AppError):
    """Raised when an external market data provider call fails."""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_ERROR",
        retryable: bool = False,
        request_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.request_count = request_count


class CollectionError(AppError):
    """Raised when a collection task cannot produce a complete raw asset."""

    code = "collection_error"

    def __init__(self, message: str, *, error_code: str, retryable: bool) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class BatchPlanningError(AppError):
    """Raised when a collection batch cannot be planned or frozen consistently."""

    code = "batch_planning_error"


class ClosedBatchPlanMismatchError(BatchPlanningError):
    """Raised when a completed batch is replayed with a different task plan."""

    code = "closed_batch_plan_mismatch"


class CalendarCoverageError(AppError):
    """Raised when a scheduled trading-day decision lacks local calendar data."""

    code = "calendar_coverage_error"


class ProcessingError(AppError):
    """Raised when a dataset cannot pass preparation or publication."""

    code = "processing_error"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class RawAssetError(AppError):
    """Raised when an immutable raw asset cannot be written or verified."""

    code = "raw_asset_error"


class RawAssetAlreadyExistsError(RawAssetError):
    """Raised when code attempts to overwrite a sealed asset."""

    code = "raw_asset_already_exists"


class RawAssetVerificationError(RawAssetError):
    """Raised when a sealed asset fails its hash, schema, or row-count check."""

    code = "raw_asset_verification_error"

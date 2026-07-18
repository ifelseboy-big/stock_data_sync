class AppError(Exception):
    """Base exception for expected application failures."""

    code = "application_error"


class ConfigurationError(AppError):
    """Raised when a required runtime setting is missing or invalid."""

    code = "configuration_error"


class ProviderError(AppError):
    """Raised when an external market data provider call fails."""

    code = "provider_error"

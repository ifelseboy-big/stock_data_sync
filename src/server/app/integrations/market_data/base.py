from typing import Any, Protocol

type MarketRecord = dict[str, Any]


class MarketDataProvider(Protocol):
    """Stable boundary between stock modules and vendor-specific SDKs."""

    name: str

    def query(self, api_name: str, **params: object) -> list[MarketRecord]: ...

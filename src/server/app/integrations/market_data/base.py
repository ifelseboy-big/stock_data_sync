from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class ProviderQueryResult:
    table: pa.Table
    request_count: int


class MarketDataProvider(Protocol):
    """Stable boundary between stock modules and vendor-specific SDKs."""

    name: str

    def query(
        self,
        api_name: str,
        *,
        fields: tuple[str, ...],
        schema: pa.Schema,
        endpoint_budget_per_minute: int | None = None,
        **params: object,
    ) -> ProviderQueryResult: ...

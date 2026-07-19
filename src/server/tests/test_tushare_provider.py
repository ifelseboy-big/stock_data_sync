from datetime import date

import pandas as pd
import pyarrow as pa
import pytest
from pydantic import SecretStr

from app.common.errors import ConfigurationError
from app.core.config import Settings
from app.integrations.market_data.tushare_provider import TushareProvider
from app.observability.provider_calls import ProviderCallObservation


class NoWaitRateLimiter:
    def acquire(self) -> float:
        return 0


class ObservationRecorder:
    def __init__(self) -> None:
        self.items: list[ProviderCallObservation] = []

    def record(self, observation: ProviderCallObservation) -> None:
        self.items.append(observation)


def test_tushare_provider_requires_token() -> None:
    config = Settings(tushare_token=SecretStr(""))

    with pytest.raises(ConfigurationError):
        TushareProvider(config)


def test_tushare_provider_returns_declared_arrow_schema_without_pandas_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def query(self, api_name: str, **params: object) -> pd.DataFrame:
            assert api_name == "trade_cal"
            assert params["fields"] == "exchange,cal_date,is_open"
            assert params["trade_date"] == "20260717"
            return pd.DataFrame([{"exchange": "SSE", "cal_date": "20260717", "is_open": 1}])

    monkeypatch.setattr(
        "app.integrations.market_data.tushare_provider.ts.pro_api",
        lambda token, timeout: FakeClient(),
    )
    config = Settings(tushare_token=SecretStr("test-token"), tushare_max_attempts=1)
    schema = pa.schema(
        (
            pa.field("exchange", pa.string()),
            pa.field("cal_date", pa.string()),
            pa.field("is_open", pa.int64()),
        )
    )

    recorder = ObservationRecorder()
    result = TushareProvider(
        config,
        NoWaitRateLimiter(),
        call_recorder=recorder,
    ).query(
        "trade_cal",
        fields=tuple(schema.names),
        schema=schema,
        trade_date=date(2026, 7, 17),
    )

    assert result.request_count == 1
    assert result.table.schema.equals(schema, check_metadata=True)
    assert len(recorder.items) == 1
    assert recorder.items[0].endpoint == "trade_cal"
    assert recorder.items[0].status == "SUCCESS"
    assert recorder.items[0].row_count == 1

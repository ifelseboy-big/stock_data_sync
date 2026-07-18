import pytest
from pydantic import SecretStr

from app.common.errors import ConfigurationError
from app.core.config import Settings
from app.integrations.market_data.tushare_provider import TushareProvider


def test_tushare_provider_requires_token() -> None:
    config = Settings(tushare_token=SecretStr(""))

    with pytest.raises(ConfigurationError):
        TushareProvider(config)

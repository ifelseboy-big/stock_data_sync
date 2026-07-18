from time import perf_counter
from typing import Any, cast

import tushare as ts
from requests import RequestException
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.common.errors import ConfigurationError, ProviderError
from app.core.config import Settings, settings
from app.integrations.market_data.base import MarketRecord
from app.integrations.rate_limit import (
    RateLimitPolicy,
    RequestRateLimiter,
    rate_limiter_registry,
)
from app.observability.metrics import (
    PROVIDER_CALLS,
    PROVIDER_DURATION,
    PROVIDER_QUERY_DURATION,
    PROVIDER_RATE_LIMIT_WAIT,
    PROVIDER_THROTTLED,
)


class TushareProvider:
    """Tushare Pro adapter. All SDK-specific DataFrame handling stays here."""

    name = "tushare"

    def __init__(
        self,
        config: Settings = settings,
        rate_limiter: RequestRateLimiter | None = None,
    ) -> None:
        token = config.tushare_token.get_secret_value()
        if not token:
            raise ConfigurationError("TUSHARE_TOKEN is required")

        self._client: Any = ts.pro_api(token, timeout=config.tushare_timeout_seconds)
        self._rate_limiter = rate_limiter or rate_limiter_registry.get(
            self.name,
            RateLimitPolicy(
                requests=config.tushare_request_budget_per_minute,
                window_seconds=60,
            ),
        )
        self._retry = Retrying(
            stop=stop_after_attempt(config.tushare_max_attempts),
            wait=wait_exponential_jitter(
                initial=config.tushare_retry_wait_seconds,
                max=30,
                jitter=1,
            ),
            retry=retry_if_exception_type(RequestException),
            reraise=True,
        )

    def query(self, api_name: str, **params: object) -> list[MarketRecord]:
        started_at = perf_counter()
        status = "success"
        try:

            def request() -> Any:
                waited = self._rate_limiter.acquire()
                if waited > 0:
                    PROVIDER_THROTTLED.labels(provider=self.name).inc()
                    PROVIDER_RATE_LIMIT_WAIT.labels(provider=self.name).observe(waited)

                request_started_at = perf_counter()
                request_status = "success"
                try:
                    return self._client.query(api_name, **params)
                except Exception:
                    request_status = "error"
                    raise
                finally:
                    PROVIDER_CALLS.labels(
                        provider=self.name,
                        endpoint=api_name,
                        status=request_status,
                    ).inc()
                    PROVIDER_DURATION.labels(
                        provider=self.name,
                        endpoint=api_name,
                    ).observe(perf_counter() - request_started_at)

            frame = self._retry(request)
            records = cast(list[MarketRecord], frame.to_dict(orient="records"))
            return records
        except Exception as exc:
            status = "error"
            raise ProviderError(f"Tushare API {api_name} failed") from exc
        finally:
            duration = perf_counter() - started_at
            PROVIDER_QUERY_DURATION.labels(
                provider=self.name,
                endpoint=api_name,
                status=status,
            ).observe(duration)

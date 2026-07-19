from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any

import pyarrow as pa
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
from app.integrations.market_data.base import ProviderQueryResult
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
from app.observability.provider_calls import (
    NullProviderCallRecorder,
    ProviderCallObservation,
    ProviderCallRecorder,
)


class TushareProvider:
    """Tushare Pro adapter. All SDK-specific DataFrame handling stays here."""

    name = "tushare"

    def __init__(
        self,
        config: Settings = settings,
        rate_limiter: RequestRateLimiter | None = None,
        call_recorder: ProviderCallRecorder | None = None,
    ) -> None:
        token = config.tushare_token.get_secret_value()
        if not token:
            raise ConfigurationError("TUSHARE_TOKEN is required")

        self._client: Any = ts.pro_api(token, timeout=config.tushare_timeout_seconds)
        self._call_recorder = call_recorder or NullProviderCallRecorder()
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

    def query(
        self,
        api_name: str,
        *,
        fields: tuple[str, ...],
        schema: pa.Schema,
        endpoint_budget_per_minute: int | None = None,
        **params: object,
    ) -> ProviderQueryResult:
        started_at = perf_counter()
        status = "success"
        request_count = 0
        endpoint_limiter = (
            rate_limiter_registry.get(
                f"{self.name}:{api_name}",
                RateLimitPolicy(requests=endpoint_budget_per_minute, window_seconds=60),
            )
            if endpoint_budget_per_minute is not None
            else None
        )
        try:

            def request() -> pa.Table:
                nonlocal request_count
                request_count += 1
                waited = endpoint_limiter.acquire() if endpoint_limiter is not None else 0.0
                waited += self._rate_limiter.acquire()
                if waited > 0:
                    PROVIDER_THROTTLED.labels(provider=self.name).inc()
                    PROVIDER_RATE_LIMIT_WAIT.labels(provider=self.name).observe(waited)

                request_started_at = perf_counter()
                requested_at = datetime.now(UTC)
                request_status = "success"
                request_error_code: str | None = None
                row_count: int | None = None
                try:
                    frame = self._client.query(
                        api_name,
                        fields=",".join(fields),
                        **_serialize_tushare_params(params),
                    )
                    actual_fields = tuple(str(column) for column in frame.columns)
                    if actual_fields != fields:
                        raise ProviderError(
                            f"Tushare API {api_name} returned fields {actual_fields!r}; "
                            f"expected {fields!r}",
                            error_code="SCHEMA_CHANGED",
                            retryable=False,
                            request_count=request_count,
                        )
                    try:
                        table = pa.Table.from_pandas(
                            frame,
                            schema=schema,
                            preserve_index=False,
                            safe=True,
                        ).replace_schema_metadata(schema.metadata)
                    except Exception as exc:
                        raise ProviderError(
                            f"Tushare API {api_name} values do not match the declared Arrow schema",
                            error_code="SCHEMA_CHANGED",
                            retryable=False,
                            request_count=request_count,
                        ) from exc
                    row_count = table.num_rows
                    return table
                except ProviderError as exc:
                    request_status = "error"
                    request_error_code = exc.error_code
                    raise
                except RequestException:
                    request_status = "error"
                    request_error_code = "NETWORK_ERROR"
                    raise
                except Exception as exc:
                    request_status = "error"
                    request_error_code = _classify_tushare_error(str(exc))
                    raise
                finally:
                    finished_at = datetime.now(UTC)
                    duration_ms = max(0, round((perf_counter() - request_started_at) * 1000))
                    PROVIDER_CALLS.labels(
                        provider=self.name,
                        endpoint=api_name,
                        status=request_status,
                    ).inc()
                    PROVIDER_DURATION.labels(
                        provider=self.name,
                        endpoint=api_name,
                    ).observe(duration_ms / 1000)
                    self._call_recorder.record(
                        ProviderCallObservation(
                            provider=self.name,
                            endpoint=api_name,
                            requested_at=requested_at,
                            finished_at=finished_at,
                            status=request_status.upper(),
                            duration_ms=duration_ms,
                            rate_limit_wait_ms=max(0, round(waited * 1000)),
                            row_count=row_count,
                            error_code=request_error_code,
                        )
                    )

            table = self._retry(request)
            return ProviderQueryResult(table=table, request_count=request_count)
        except ProviderError:
            status = "error"
            raise
        except RequestException as exc:
            status = "error"
            raise ProviderError(
                f"Tushare API {api_name} network request failed",
                error_code="NETWORK_ERROR",
                retryable=True,
                request_count=request_count,
            ) from exc
        except Exception as exc:
            status = "error"
            detail = _upstream_error_detail(exc)
            error_code = _classify_tushare_error(detail)
            raise ProviderError(
                f"Tushare API {api_name} failed: {detail}",
                error_code=error_code,
                retryable=error_code == "RATE_LIMITED",
                request_count=request_count,
            ) from exc
        finally:
            duration = perf_counter() - started_at
            PROVIDER_QUERY_DURATION.labels(
                provider=self.name,
                endpoint=api_name,
                status=status,
            ).observe(duration)


def _classify_tushare_error(message: str) -> str:
    normalized = message.casefold()
    classifications = (
        (("每分钟", "访问"), "RATE_LIMITED"),
        (("访问频次",), "RATE_LIMITED"),
        (("访问次数", "超过"), "RATE_LIMITED"),
        (("rate limit",), "RATE_LIMITED"),
        (("too many requests",), "RATE_LIMITED"),
        (("token", "无效"), "TOKEN_INVALID"),
        (("token", "invalid"), "TOKEN_INVALID"),
        (("权限",), "PERMISSION_DENIED"),
        (("permission",), "PERMISSION_DENIED"),
        (("参数",), "INVALID_PARAMETER"),
        (("parameter",), "INVALID_PARAMETER"),
        (("字段",), "SCHEMA_CHANGED"),
        (("field",), "SCHEMA_CHANGED"),
    )
    for fragments, code in classifications:
        if all(fragment in normalized for fragment in fragments):
            return code
    return "PROVIDER_ERROR"


def _upstream_error_detail(exc: Exception) -> str:
    detail = " ".join(str(exc).split())
    return (detail or type(exc).__name__)[:500]


def _serialize_tushare_params(params: dict[str, object]) -> dict[str, object]:
    return {
        key: value.strftime("%Y%m%d") if isinstance(value, date) else value
        for key, value in params.items()
    }

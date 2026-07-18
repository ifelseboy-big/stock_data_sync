from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "stock_sync_http_requests_total",
    "HTTP requests processed by the API",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "stock_sync_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route"],
)
PROVIDER_CALLS = Counter(
    "stock_sync_provider_calls_total",
    "Market data provider calls",
    ["provider", "endpoint", "status"],
)
PROVIDER_DURATION = Histogram(
    "stock_sync_provider_call_duration_seconds",
    "Physical market data provider request duration in seconds",
    ["provider", "endpoint"],
)
PROVIDER_QUERY_DURATION = Histogram(
    "stock_sync_provider_query_duration_seconds",
    "Logical provider query duration including throttling and retries",
    ["provider", "endpoint", "status"],
)
PROVIDER_THROTTLED = Counter(
    "stock_sync_provider_throttled_total",
    "Provider requests delayed by the local rate limiter",
    ["provider"],
)
PROVIDER_RATE_LIMIT_WAIT = Histogram(
    "stock_sync_provider_rate_limit_wait_seconds",
    "Time spent waiting for a provider request slot",
    ["provider"],
)
SCHEDULED_JOBS = Counter(
    "stock_sync_scheduled_jobs_total",
    "Scheduled job outcomes",
    ["job_id", "status"],
)

from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = SERVER_DIR.parents[1] if SERVER_DIR.parent.name == "src" else SERVER_DIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Stock Data Sync"
    app_version: str = "dev"
    app_debug: bool = False
    app_api_prefix: str = "/api/v1"
    app_cors_origins: list[AnyHttpUrl] = [AnyHttpUrl("http://localhost:5173")]
    app_log_file: Path | None = None
    app_log_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024 * 1024)
    app_log_backup_count: int = Field(default=10, ge=1, le=100)

    database_url: str = "postgresql+psycopg://stock_sync:stock_sync@localhost:5432/stock_data_sync"
    mcp_database_url: str | None = None
    mcp_query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    raw_data_dir: Path = ROOT_DIR / "data" / "raw"
    raw_storage_warning_used_percent: float = Field(default=85, ge=1, le=99)
    raw_storage_protect_used_percent: float = Field(default=92, ge=1, le=99)
    raw_storage_warning_free_bytes: int = Field(default=20 * 1024**3, ge=0)
    raw_storage_protect_free_bytes: int = Field(default=10 * 1024**3, ge=0)
    web_dist_dir: Path | None = None
    admin_api_token: SecretStr = SecretStr("")

    tushare_token: SecretStr = SecretStr("")
    tushare_request_limit_per_minute: int = Field(default=500, ge=1)
    tushare_request_budget_per_minute: int = Field(default=480, ge=1)
    tushare_timeout_seconds: float = Field(default=30, ge=1, le=300)
    tushare_max_attempts: int = Field(default=3, ge=1, le=10)
    tushare_retry_wait_seconds: float = Field(default=2, ge=0.1, le=60)
    market_index_codes: tuple[str, ...] = (
        "000001.SH",
        "399001.SZ",
        "000016.SH",
        "000300.SH",
        "000905.SH",
        "399006.SZ",
    )

    scheduler_timezone: str = "Asia/Shanghai"
    scheduler_jobstore_table: str = "apscheduler_jobs"
    scheduler_advisory_lock_id: int = 731_500_001
    processing_advisory_lock_id: int = 731_500_002
    scheduler_max_workers: int = Field(default=4, ge=1, le=32)
    scheduler_poll_seconds: int = Field(default=10, ge=5, le=300)
    scheduler_execution_retention_days: int = Field(default=30, ge=7, le=365)
    partition_months_ahead: int = Field(default=3, ge=0, le=24)
    collection_max_workers: int = Field(default=4, ge=1, le=16)
    collection_running_timeout_seconds: int = Field(default=1800, ge=60, le=86400)
    processing_max_workers: int = Field(default=3, ge=1, le=8)
    processing_running_timeout_seconds: int = Field(default=21600, ge=300, le=172800)

    @model_validator(mode="after")
    def validate_tushare_request_budget(self) -> "Settings":
        if self.tushare_request_budget_per_minute > self.tushare_request_limit_per_minute:
            raise ValueError("Tushare request budget cannot exceed the provider limit")
        if not self.market_index_codes or len(set(self.market_index_codes)) != len(
            self.market_index_codes
        ):
            raise ValueError("MARKET_INDEX_CODES must be non-empty and unique")
        if self.raw_storage_warning_used_percent >= self.raw_storage_protect_used_percent:
            raise ValueError("raw storage warning percent must be less than protect percent")
        if self.raw_storage_warning_free_bytes <= self.raw_storage_protect_free_bytes:
            raise ValueError("raw storage warning free bytes must exceed protect free bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

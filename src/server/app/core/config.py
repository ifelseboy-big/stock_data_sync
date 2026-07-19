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
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Stock Data Sync"
    app_debug: bool = False
    app_api_prefix: str = "/api/v1"
    app_cors_origins: list[AnyHttpUrl] = [AnyHttpUrl("http://localhost:5173")]
    app_log_file: Path | None = None
    app_log_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024 * 1024)
    app_log_backup_count: int = Field(default=10, ge=1, le=100)

    database_url: str = "postgresql+psycopg://stock_sync:stock_sync@localhost:5432/stock_data_sync"
    raw_data_dir: Path = ROOT_DIR / "data" / "raw"
    web_dist_dir: Path | None = None

    tushare_token: SecretStr = SecretStr("")
    tushare_request_limit_per_minute: int = Field(default=500, ge=1)
    tushare_request_budget_per_minute: int = Field(default=480, ge=1)
    tushare_timeout_seconds: float = Field(default=30, ge=1, le=300)
    tushare_max_attempts: int = Field(default=3, ge=1, le=10)
    tushare_retry_wait_seconds: float = Field(default=2, ge=0.1, le=60)

    scheduler_timezone: str = "Asia/Shanghai"
    scheduler_jobstore_table: str = "apscheduler_jobs"
    scheduler_advisory_lock_id: int = 731_500_001
    scheduler_max_workers: int = Field(default=4, ge=1, le=32)
    scheduler_poll_seconds: int = Field(default=30, ge=5, le=300)

    @model_validator(mode="after")
    def validate_tushare_request_budget(self) -> "Settings":
        if self.tushare_request_budget_per_minute > self.tushare_request_limit_per_minute:
            raise ValueError("Tushare request budget cannot exceed the provider limit")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

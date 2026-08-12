"""Environment-backed settings shared by the API and worker processes."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSPILOT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "opspilot:runs"
    graph_version: str = "opspilot-runtime-v1"
    config_version: str = "default-v1"
    checkpoint_schema_version: str = "1.0"
    recovery_stale_seconds: float = Field(default=30.0, ge=0)
    queue_poll_seconds: float = Field(default=1.0, gt=0)
    retry_backoff_seconds: float = Field(default=0.05, ge=0)
    recovery_scan_seconds: float = Field(default=5.0, gt=0)
    queue_repair_seconds: float = Field(default=30.0, ge=0)
    tool_timeout_seconds: float = Field(default=0.2, gt=0)
    tool_max_attempts: int = Field(default=2, ge=1, le=10)
    mock_base_url: str = "http://localhost:8001"

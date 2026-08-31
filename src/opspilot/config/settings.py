"""Environment-backed settings shared by the API and worker processes."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSPILOT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "opspilot:runs"
    graph_version: str = "opspilot-runtime-v4-unified-evidence"
    config_version: str = "adaptive-v2"
    checkpoint_schema_version: str = "1.0"
    recovery_stale_seconds: float = Field(default=30.0, ge=0)
    queue_poll_seconds: float = Field(default=1.0, gt=0)
    retry_backoff_seconds: float = Field(default=0.05, ge=0)
    recovery_scan_seconds: float = Field(default=5.0, gt=0)
    queue_repair_seconds: float = Field(default=30.0, ge=0)
    tool_timeout_seconds: float = Field(default=0.2, gt=0)
    tool_max_attempts: int = Field(default=2, ge=1, le=10)
    investigation_max_rounds: int = Field(default=4, ge=1, le=20)
    investigation_max_tool_calls: int = Field(default=8, ge=1, le=100)
    investigation_max_expert_calls: int = Field(default=2, ge=0, le=20)
    evidence_gate_confidence: float = Field(default=0.8, ge=0, le=1)
    evidence_gate_margin: float = Field(default=0.15, ge=0, le=1)
    evidence_gate_min_sources: int = Field(default=2, ge=1, le=10)
    mock_base_url: str = "http://localhost:8001"
    llm_enabled: bool = False
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = Field(default=30.0, gt=0)

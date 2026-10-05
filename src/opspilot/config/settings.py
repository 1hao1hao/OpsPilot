"""Environment-backed settings shared by the API and worker processes."""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSPILOT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "opspilot:runs"
    graph_version: str = "opspilot-runtime-v6-compact-report"
    config_version: str = "adaptive-v2"
    checkpoint_schema_version: str = "3.0"
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

    observation_backend: Literal["mock", "otel_demo"] = "mock"
    prometheus_url: str = ""
    jaeger_url: str = ""
    opensearch_url: str = ""
    opensearch_index: str = "otel-logs-*"
    telemetry_timeout_seconds: float = Field(default=5, gt=0)
    telemetry_window_before_seconds: int = Field(default=900, ge=60, le=86400)
    telemetry_window_after_seconds: int = Field(default=60, ge=1, le=3600)
    telemetry_step_seconds: int = Field(default=15, ge=1)
    telemetry_log_limit: int = Field(default=50, ge=1, le=200)
    telemetry_trace_limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def live_backends_required(self):
        if self.observation_backend == "otel_demo":
            for name in ("prometheus_url", "jaeger_url", "opensearch_url"):
                if not getattr(self, name).startswith(("http://", "https://")):
                    raise ValueError(f"{name} must be an explicit HTTP(S) URL for otel_demo")
            if "tool_timeout_seconds" not in self.model_fields_set:
                # Discovery + range queries need a network budget; preserve explicit user limits.
                self.tool_timeout_seconds = 30.0
        return self

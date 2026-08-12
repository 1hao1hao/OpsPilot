"""配置系统。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Settings (pydantic-settings)</td><td>REQ: 20260713-总体架构, TECH: 04b §3.2</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，从 .env 文件读取。

    使用标准 Redis 和 Kafka（非公司内部服务）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="development", description="运行环境")
    app_host: str = Field(default="0.0.0.0", description="监听地址")
    app_port: int = Field(default=8000, description="监听端口")
    app_external_host: str = Field(default="localhost", description="外部访问地址（用于生成反馈/通知 URL）")
    agent_url: str = Field(default="http://localhost:8000", description="Agent 服务访问地址（Mock容器内调用时使用）")
    log_level: str = Field(default="INFO", description="日志级别")

    # LLM
    llm_api_base: str = Field(default="http://localhost:11434/v1", description="LLM API 地址")
    llm_api_key: str = Field(default="", description="LLM API Key")
    llm_model: str = Field(default="gpt-4o", description="LLM 模型名")
    llm_max_tokens: int = Field(default=4096, description="LLM 最大 token 数")
    llm_timeout: int = Field(default=30, description="LLM 调用超时（秒）")

    # Redis (standard)
    redis_host: str = Field(default="localhost", description="Redis 地址")
    redis_port: int = Field(default=6379, description="Redis 端口")
    redis_db: int = Field(default=0, description="Redis 数据库")
    redis_password: str = Field(default="", description="Redis 密码")

    # Kafka (standard)
    kafka_bootstrap_servers: str = Field(default="localhost:9092", description="Kafka 地址")
    kafka_feedback_topic: str = Field(default="deeprca-feedback", description="反馈 topic")

    # Analysis
    analysis_timeout: int = Field(default=60, description="端到端分析超时（秒）")
    tool_call_timeout: int = Field(default=10, description="单次工具调用超时（秒）")
    max_concurrent_tasks: int = Field(default=10, description="最大并发任务数")

    # Mock Environment
    mock_env_enabled: bool = Field(default=True, description="是否启用 Mock 环境")
    mock_k8s_api: str = Field(default="http://localhost:8001", description="Mock K8s API")
    # 以下三个配置在 mock_env_enabled=True 时被使用（工具层和 L2 专家通过 HTTP 调用 Mock API 获取场景感知数据），
    # 仅在对接真实监控系统时需要改为实际监控服务地址。统一指向 8001 以避免端口混淆。
    mock_monitor_api: str = Field(default="http://localhost:8001", description="Mock 监控 API（mock_env_enabled=True 时被工具层和 L2 专家使用）")
    mock_log_api: str = Field(default="http://localhost:8001", description="Mock 日志 API（当前与 mock_monitor_api 统一指向 8001）")
    mock_change_api: str = Field(default="http://localhost:8001", description="Mock 变更 API（当前与 mock_monitor_api 统一指向 8001）")


@lru_cache
def get_settings() -> Settings:
    """获取全局 Settings 单例。"""
    return Settings()

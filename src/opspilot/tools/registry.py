"""Tool registry and Stage-1 read-only snapshot/HTTP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from opspilot.models import AlertEvent
from opspilot.tools.errors import UnknownToolError

ToolHandler = Callable[[BaseModel], Awaitable[BaseModel]]


class AlertToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert: AlertEvent


class ObservationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    name: str
    version: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    timeout_seconds: float = Field(default=3.0, gt=0)
    max_attempts: int = Field(default=1, ge=1)
    idempotent: bool = True
    side_effect: bool = False
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        if definition.side_effect:
            raise ValueError("Stage-1 registry accepts read-only tools only")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._definitions)


TOOL_SIGNAL_KEYS = {
    "metrics.query": "metric",
    "logs.query": "log",
    "changes.query": "change",
    "traces.query": "trace",
    "topology.query": "topology",
    "db.inspect": "db",
    "redis.inspect": "redis",
    "kafka.inspect": "kafka",
    "rpc.inspect": "rpc",
}


class ObservationProvider:
    """Returns observed data; benchmark snapshots take precedence over HTTP."""

    def __init__(self, mock_base_url: str = "http://localhost:8001") -> None:
        self.mock_base_url = mock_base_url.rstrip("/")

    async def read(self, signal_key: str, alert: AlertEvent) -> dict[str, Any]:
        # A non-empty signals mapping is a complete immutable snapshot. Missing
        # sources mean "observed empty", not "fall through to live HTTP".
        if alert.signals:
            return alert.signals.get(signal_key, {})
        return await self._read_mock_http(signal_key, alert)

    async def _read_mock_http(self, signal_key: str, alert: AlertEvent) -> dict[str, Any]:
        service = alert.service_name
        routes = {
            "metric": f"/api/v1/mock/service/{service}/metrics/tp99",
            "log": f"/api/v1/mock/service/{service}/logs",
            "change": f"/api/v1/mock/service/{service}/changes",
            "trace": f"/api/v1/mock/service/{service}/traces",
            "topology": "/api/v1/mock/service/topology",
            "db": "/api/v1/mock/db/mysql-prod-01/metrics",
            "redis": "/api/v1/mock/redis/redis-cluster-01/metrics",
            "kafka": "/api/v1/mock/kafka/kafka-prod-01/metrics",
            "rpc": f"/api/v1/mock/service/{service}/metrics/error_rate",
        }
        route = routes[signal_key]
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(f"{self.mock_base_url}{route}")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"mock source unavailable for {signal_key}: {exc}") from exc
        return payload if isinstance(payload, dict) else {"items": payload}


def build_default_registry(
    *,
    provider: ObservationProvider | None = None,
    timeout_seconds: float = 2.0,
    max_attempts: int = 1,
) -> ToolRegistry:
    provider = provider or ObservationProvider()
    registry = ToolRegistry()

    for tool_name, signal_key in TOOL_SIGNAL_KEYS.items():
        async def handler(payload: AlertToolInput, key: str = signal_key) -> ObservationOutput:
            return ObservationOutput(observations=await provider.read(key, payload.alert))

        registry.register(
            ToolDefinition(
                name=tool_name,
                version="1.0",
                description=f"Read-only {signal_key} observations for RCA",
                input_schema=AlertToolInput,
                output_schema=ObservationOutput,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                handler=handler,
            )
        )
    return registry

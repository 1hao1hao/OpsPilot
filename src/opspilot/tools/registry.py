"""Tool registry and Stage-1 read-only snapshot/HTTP adapters."""

from __future__ import annotations

import asyncio
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
    "alerts.query": "problem",
}

DOMAIN_TOOL_FIELDS = {
    "db.replication": ("replication_lag_seconds", "slave_delay_seconds"),
    "db.slowlog": ("slow_query_count", "slow_queries"),
    "db.connections": ("active_connections", "max_connections"),
    "redis.memory": ("memory_usage_percent", "used_memory", "evicted_keys"),
    "redis.hotkeys": ("hit_rate_percent", "hit_rate", "hotkeys"),
    "kafka.lag": ("consumer_lag", "total_lag", "produce_rate", "consume_rate"),
    "rpc.metrics": ("timeout_rate", "error_rate", "latency_ms", "baseline_latency_ms", "call_volume"),
}

DOMAIN_TOOL_SOURCES = {
    "db.replication": "db",
    "db.slowlog": "db",
    "db.connections": "db",
    "redis.memory": "redis",
    "redis.hotkeys": "redis",
    "kafka.lag": "kafka",
    "rpc.metrics": "rpc",
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
        if signal_key in {"metric", "rpc"}:
            metric_names = ("qps", "error_rate", "tp99", "cpu_usage", "memory_usage")
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    responses = await asyncio.gather(*(
                        client.get(f"{self.mock_base_url}/api/v1/mock/service/{service}/metrics/{name}")
                        for name in metric_names
                    ))
                    for response in responses:
                        response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"mock source unavailable for {signal_key}: {exc}") from exc
            payloads = {name: response.json() for name, response in zip(metric_names, responses, strict=True)}
            if signal_key == "metric":
                return payloads

            def latest(name: str) -> float:
                points = payloads[name].get("data_points", [])
                return float(points[-1].get("value", 0)) if points else 0.0

            error_rate_percent = latest("error_rate")
            latency = latest("tp99")
            baseline = float(payloads["tp99"].get("aggregation", {}).get("baseline", 0))
            return {
                "error_rate": error_rate_percent / 100.0,
                "timeout_rate": 0.0,
                "latency_ms": latency,
                "baseline_latency_ms": baseline,
                "call_volume": latest("qps"),
            }
        routes = {
            "log": f"/api/v1/mock/service/{service}/logs",
            "change": f"/api/v1/mock/service/{service}/changes",
            "trace": f"/api/v1/mock/service/{service}/traces",
            "topology": "/api/v1/mock/service/topology",
            "db": "/api/v1/mock/db/mysql-prod-01/metrics",
            "redis": "/api/v1/mock/redis/redis-cluster-01/metrics",
            "kafka": "/api/v1/mock/kafka/kafka-prod-01/metrics",
            "problem": f"/api/v1/mock/service/{service}/alerts",
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

    for tool_name, signal_key in DOMAIN_TOOL_SOURCES.items():
        async def domain_handler(
            payload: AlertToolInput,
            name: str = tool_name,
            key: str = signal_key,
        ) -> ObservationOutput:
            source = await provider.read(key, payload.alert)
            fields = DOMAIN_TOOL_FIELDS[name]
            return ObservationOutput(observations={field: source[field] for field in fields if field in source})

        registry.register(
            ToolDefinition(
                name=tool_name,
                version="1.0",
                description=f"Read-only adaptive {tool_name} drill-down",
                input_schema=AlertToolInput,
                output_schema=ObservationOutput,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                handler=domain_handler,
            )
        )
    return registry

"""Opt-in read-only smoke against a running, pinned Astronomy Shop."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, ToolCall, ToolStatus
from opspilot.observations.provider import OpenTelemetryDemoProvider
from opspilot.tools.executor import ToolExecutor
from opspilot.tools.registry import build_default_registry


@pytest.mark.skipif(
    os.getenv("OPSPILOT_OTEL_LIVE_TEST") != "1", reason="SKIPPED: live pinned Astronomy Shop not enabled"
)
async def test_live_tools():
    settings = RuntimeSettings(observation_backend="otel_demo", tool_timeout_seconds=30)
    registry = build_default_registry(settings=settings, timeout_seconds=30)
    executor = ToolExecutor(registry)
    alert = AlertEvent(
        alert_id="live-smoke",
        service_name="frontend",
        alert_type="custom",
        severity="P2",
        # Exporters batch telemetry; allow the incident window to have reached the backends.
        timestamp=datetime.fromtimestamp(datetime.now(UTC).timestamp() - 120, UTC),
        description="frontend telemetry smoke",
    )
    output = {}
    for tool in registry.names():
        result = await executor.execute(
            ToolCall(tool_call_id=tool, tool_name=tool, arguments={"alert": alert.model_dump(mode="json")})
        )
        output[tool] = result.model_dump(mode="json")
    path = Path("artifacts/otel_demo/live_smoke.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2) + "\n")
    provider = OpenTelemetryDemoProvider(settings)
    discovery = await provider.prometheus.discover(*provider.window(alert), alert.service_name)
    discovery_path = path.parent / "discovery/metrics.json"
    discovery_path.parent.mkdir(parents=True, exist_ok=True)
    discovery_path.write_text(json.dumps(discovery, indent=2) + "\n")
    expected = {
        "metrics.query": ["qps", "error_rate", "tp99", "cpu_usage", "memory_usage"],
        "db.connections": ["active_connections", "max_connections"],
        "redis.memory": ["used_memory", "memory_usage_percent"],
        "redis.hotkeys": ["hit_rate_percent", "hotkeys"],
        "kafka.lag": ["consumer_lag"],
        "rpc.metrics": ["timeout_rate", "error_rate", "latency_ms", "baseline_latency_ms", "call_volume"],
    }
    gaps = {
        name: [field for field in fields if field not in (output[name].get("data") or {}).get("observations", {})]
        for name, fields in expected.items()
    }
    (path.parent / "telemetry_gaps.json").write_text(json.dumps(gaps, indent=2) + "\n")
    assert all(r["status"] == ToolStatus.SUCCESS.value for r in output.values()), output
    for tool in ("metrics.query", "logs.query", "traces.query"):
        assert any(output[tool]["data"]["observations"].values()), f"No actual telemetry: {tool}"
    for tool, field in (
        ("kafka.lag", "consumer_lag"),
        ("db.connections", "max_connections"),
        ("redis.memory", "used_memory"),
        ("redis.hotkeys", "hit_rate_percent"),
    ):
        assert field in output[tool]["data"]["observations"], f"No receiver telemetry: {tool}.{field}"

import asyncio
from datetime import UTC, datetime

from opspilot.config import RuntimeSettings
from opspilot.graph.workflow import OpsPilotWorkflow
from opspilot.models import AlertEvent, ToolStatus
from opspilot.tools.mcp_bridge import MCPToolGateway, build_observability_mcp
from opspilot.tools.registry import build_default_registry


def alert():
    return AlertEvent(alert_id="mcp-contract", service_name="checkout", alert_type="timeout",
                      severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
                      signals={"metric": {"cpu_usage": 0.82}})


async def test_official_mcp_discovery_and_call_matches_native_observation():
    registry = build_default_registry()
    gateway = MCPToolGateway(build_observability_mcp(registry))
    result = await gateway.execute("metrics.query", alert(), "mcp-call-1")
    assert result.status == ToolStatus.SUCCESS
    assert result.data == {"observations": {"cpu_usage": 0.82}}
    denied = await gateway.execute("db.slowlog", alert(), "mcp-call-2")
    assert denied.status == ToolStatus.ERROR
    assert denied.error_code == "mcp_unauthorized"


async def test_mcp_timeout_and_unavailable_retry_are_bounded():
    gateway = MCPToolGateway(build_observability_mcp(build_default_registry()),
                             timeout_seconds=0.01, max_attempts=2)
    calls = 0

    async def unavailable(*_args):
        nonlocal calls
        calls += 1
        raise ConnectionError("local MCP transport unavailable")

    gateway._call = unavailable
    failed = await gateway.execute("metrics.query", alert(), "mcp-retry")
    assert (failed.error_code, failed.attempt, calls) == ("mcp_unavailable", 2, 2)

    async def slow(*_args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)

    gateway._call = slow
    timed = await gateway.execute("metrics.query", alert(), "mcp-timeout")
    assert (timed.error_code, timed.attempt, calls) == ("mcp_timeout", 2, 4)


async def test_mcp_unknown_tool_and_invalid_schema_fail_without_retry():
    gateway = MCPToolGateway(build_observability_mcp(build_default_registry()))

    async def unknown(*_args):
        raise ValueError("unknown MCP tool")

    gateway._call = unknown
    unknown_result = await gateway.execute("metrics.query", alert(), "mcp-unknown")
    assert (unknown_result.error_code, unknown_result.attempt) == ("mcp_unknown_tool", 1)

    async def invalid(*_args):
        raise TypeError("invalid MCP observation schema")

    gateway._call = invalid
    invalid_result = await gateway.execute("metrics.query", alert(), "mcp-invalid")
    assert (invalid_result.error_code, invalid_result.attempt) == ("mcp_schema_error", 1)


async def test_mcp_transport_changes_actual_workflow_tool_calls_with_same_observations():
    registry = build_default_registry()
    native = OpsPilotWorkflow(registry, settings=RuntimeSettings(
        tool_transport="native", investigation_max_rounds=1))
    mcp = OpsPilotWorkflow(registry, settings=RuntimeSettings(
        tool_transport="mcp", investigation_max_rounds=1))
    left = await native.observe(alert(), trace_id="same-transport-case")
    right = await mcp.observe(alert(), trace_id="same-transport-case")
    assert sorted((item.tool_name, item.data, item.status) for item in left.tool_results) == sorted(
        (item.tool_name, item.data, item.status) for item in right.tool_results)
    assert left.provisional_candidates == right.provisional_candidates

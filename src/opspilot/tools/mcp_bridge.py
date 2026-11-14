"""Local, read-only MCP transport for existing observation tools."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from opspilot.models import AlertEvent, ToolResult, ToolStatus
from opspilot.tools.registry import ToolRegistry

MCP_GENERAL_TOOLS = frozenset({"metrics.query", "traces.query", "logs.query", "topology.query"})


def build_observability_mcp(registry: ToolRegistry) -> MCPServer:
    """Expose validated read-only Registry handlers through the official MCP SDK."""
    server = MCPServer("opspilot-observability")
    for name in sorted(MCP_GENERAL_TOOLS & set(registry.general_names())):
        definition = registry.get(name)
        if definition.side_effect or definition.tool_kind != "general":
            continue

        def handler_for(tool_name: str):
            async def observation(alert: dict[str, Any], target_service: str | None = None) -> dict[str, Any]:
                parsed = AlertEvent.model_validate(alert)
                if target_service and target_service != parsed.service_name:
                    raise ValueError("target_service outside alert scope")
                selected = registry.get(tool_name)
                payload = selected.input_schema.model_validate({"alert": parsed, "target_service": target_service})
                result = await asyncio.wait_for(selected.handler(payload), selected.timeout_seconds)
                return selected.output_schema.model_validate(result).model_dump(mode="json")
            return observation

        server.tool(name=name, description=definition.description)(handler_for(name))
    return server


class MCPToolGateway:
    """Discover and call MCP tools, translating failures into standard ToolResult."""

    def __init__(self, server: MCPServer, *, timeout_seconds: float = 3.0,
                 max_attempts: int = 2) -> None:
        if timeout_seconds <= 0 or max_attempts < 1:
            raise ValueError("MCP timeout and attempts must be positive")
        self.server = server
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    async def execute(self, tool_name: str, alert: AlertEvent, tool_call_id: str) -> ToolResult:
        started = monotonic()
        if tool_name not in MCP_GENERAL_TOOLS:
            return self._error(tool_name, tool_call_id, "mcp_unauthorized", "tool is not authorized for MCP",
                               started, 1)
        for attempt in range(1, self.max_attempts + 1):
            try:
                data = await asyncio.wait_for(self._call(tool_name, alert), self.timeout_seconds)
                return ToolResult(tool_call_id=tool_call_id, tool_name=tool_name, status=ToolStatus.SUCCESS,
                                  data=data, latency_ms=(monotonic() - started) * 1000, attempt=attempt)
            except TimeoutError as exc:
                code, message = "mcp_timeout", str(exc)
            except (ConnectionError, OSError) as exc:
                code, message = "mcp_unavailable", str(exc)
            except (TypeError, ValidationError) as exc:
                code, message = "mcp_schema_error", str(exc)
            except ValueError as exc:
                code, message = "mcp_unknown_tool", str(exc)
            except Exception as exc:  # noqa: BLE001 - SDK transport failures must become ToolResult
                code, message = "mcp_tool_error", str(exc)
            if code not in {"mcp_timeout", "mcp_unavailable"} or attempt == self.max_attempts:
                return self._error(tool_name, tool_call_id, code, message, started, attempt)
        raise AssertionError("unreachable MCP retry state")

    async def _call(self, tool_name: str, alert: AlertEvent) -> dict[str, Any]:
        async with asyncio.timeout(self.timeout_seconds):
            async with Client(self.server) as client:
                discovered = await client.list_tools()
                names = {item.name for item in discovered.tools}
                if tool_name not in names:
                    raise ValueError("unknown MCP tool")
                response = await client.call_tool(tool_name, {"alert": alert.model_dump(mode="json")})
                if response.is_error:
                    raise RuntimeError("MCP tool returned an error")
                data = response.structured_content
                if not isinstance(data, dict) or not isinstance(data.get("observations"), dict):
                    raise TypeError("invalid MCP observation schema")
                return data

    @staticmethod
    def _error(tool_name: str, tool_call_id: str, code: str, message: str,
               started: float, attempt: int) -> ToolResult:
        return ToolResult(tool_call_id=tool_call_id, tool_name=tool_name, status=ToolStatus.ERROR,
                          error_code=code, error_message=message,
                          latency_ms=(monotonic() - started) * 1000, attempt=attempt)

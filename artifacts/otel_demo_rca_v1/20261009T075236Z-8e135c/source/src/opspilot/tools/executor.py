"""Schema validation, timeout, retry and in-memory Stage-1 execution records."""

from __future__ import annotations

import asyncio
import hashlib
import json
from time import perf_counter

from pydantic import ValidationError

from opspilot.models import ToolCall, ToolExecution, ToolResult, ToolStatus
from opspilot.tools.errors import ToolTimeoutError, ToolValidationError
from opspilot.tools.registry import ToolRegistry


def build_tool_call_id(*, trace_id: str, step_id: str, tool_name: str, version: str, arguments: dict) -> str:
    normalized = json.dumps(arguments, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(f"{trace_id}|{step_id}|{tool_name}|{version}|{normalized}".encode()).hexdigest()
    return f"tool-{digest[:20]}"


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, *, retry_backoff_seconds: float = 0) -> None:
        self.registry = registry
        self.retry_backoff_seconds = retry_backoff_seconds
        self.executions: list[ToolExecution] = []
        self._successful: dict[str, ToolResult] = {}

    async def execute(self, call: ToolCall) -> ToolResult:
        definition = self.registry.get(call.tool_name)
        if call.tool_call_id in self._successful:
            return self._successful[call.tool_call_id]

        try:
            validated_input = definition.input_schema.model_validate(call.arguments)
        except ValidationError as exc:
            raise ToolValidationError(str(exc)) from exc

        for attempt in range(1, definition.max_attempts + 1):
            started = perf_counter()
            try:
                raw_output = await asyncio.wait_for(
                    definition.handler(validated_input),
                    timeout=definition.timeout_seconds,
                )
                output = definition.output_schema.model_validate(raw_output)
                latency_ms = (perf_counter() - started) * 1000
                result = ToolResult(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    status=ToolStatus.SUCCESS,
                    data=output.model_dump(mode="json"),
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
                self._record(result)
                self._successful[call.tool_call_id] = result
                return result
            except TimeoutError as exc:
                error: Exception = ToolTimeoutError(f"{call.tool_name} timed out")
                cause = exc
            # Registered handlers wrap arbitrary provider libraries. Their
            # errors are normalized into ToolResult at this boundary.
            except Exception as exc:  # noqa: BLE001
                error = exc
                cause = exc

            latency_ms = (perf_counter() - started) * 1000
            error_code = getattr(error, "code", "tool_execution_error")
            result = ToolResult(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                status=ToolStatus.ERROR,
                error_code=error_code,
                error_message=str(error),
                latency_ms=latency_ms,
                attempt=attempt,
            )
            self._record(result)
            if attempt == definition.max_attempts:
                return result
            if not getattr(error, "retryable", False):
                return result
            if self.retry_backoff_seconds:
                await asyncio.sleep(self.retry_backoff_seconds * attempt)
        raise cause  # pragma: no cover

    def _record(self, result: ToolResult) -> None:
        self.executions.append(
            ToolExecution(
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                status=result.status,
                latency_ms=result.latency_ms,
                attempt=result.attempt,
                error_code=result.error_code,
            )
        )

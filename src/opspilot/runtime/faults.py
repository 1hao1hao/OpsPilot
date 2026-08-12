"""Deterministic, process-local fault injection used only by reliability runs."""

from __future__ import annotations

import asyncio
import os
from enum import Enum

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent
from opspilot.tools.errors import ToolExecutionError
from opspilot.tools.registry import ObservationProvider, build_default_registry


class RuntimeFaultType(str, Enum):
    WORKER_CRASH = "WorkerCrash"
    TOOL_TIMEOUT = "ToolTimeout"
    TOOL_HTTP_500 = "ToolHTTP500"
    DUPLICATE_REQUEST = "DuplicateRequest"
    DUPLICATE_DELIVERY = "DuplicateDelivery"


class FaultInjectingProvider(ObservationProvider):
    """Inject a bounded fault before delegating to the normal snapshot provider."""

    def __init__(
        self,
        *,
        fault_type: RuntimeFaultType,
        target_signal: str,
        failure_count: int,
        timeout_seconds: float,
        mock_base_url: str,
    ) -> None:
        super().__init__(mock_base_url)
        self.fault_type = fault_type
        self.target_signal = target_signal
        self.remaining = failure_count
        self.timeout_seconds = timeout_seconds

    async def read(self, signal_key: str, alert: AlertEvent) -> dict:
        if signal_key == self.target_signal and self.remaining > 0:
            self.remaining -= 1
            if self.fault_type == RuntimeFaultType.TOOL_TIMEOUT:
                await asyncio.sleep(self.timeout_seconds * 2)
            elif self.fault_type == RuntimeFaultType.TOOL_HTTP_500:
                raise ToolExecutionError("injected HTTP 500")
        return await super().read(signal_key, alert)


def build_worker_registry(settings: RuntimeSettings):
    """Build the normal registry unless an explicit reliability fault is configured."""
    fault_value = os.getenv("OPSPILOT_FAULT_TYPE")
    provider = None
    if fault_value:
        fault_type = RuntimeFaultType(fault_value)
        if fault_type in {RuntimeFaultType.TOOL_TIMEOUT, RuntimeFaultType.TOOL_HTTP_500}:
            provider = FaultInjectingProvider(
                fault_type=fault_type,
                target_signal=os.getenv("OPSPILOT_FAULT_TARGET_SIGNAL", "db"),
                failure_count=int(os.getenv("OPSPILOT_FAULT_COUNT", "1")),
                timeout_seconds=settings.tool_timeout_seconds,
                mock_base_url=settings.mock_base_url,
            )
    return build_default_registry(
        provider=provider,
        timeout_seconds=settings.tool_timeout_seconds,
        max_attempts=settings.tool_max_attempts,
    )

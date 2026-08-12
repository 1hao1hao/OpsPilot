from __future__ import annotations

from datetime import UTC, datetime

import pytest

from opspilot.models import AlertEvent
from opspilot.runtime.faults import FaultInjectingProvider, RuntimeFaultType
from opspilot.tools.errors import ToolExecutionError


def alert() -> AlertEvent:
    return AlertEvent(
        alert_id="fault-unit",
        service_name="checkout-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        signals={"db": {"replication_lag_seconds": 20}},
    )


@pytest.mark.asyncio
async def test_http_500_injector_fails_exact_count_then_resets():
    provider = FaultInjectingProvider(
        fault_type=RuntimeFaultType.TOOL_HTTP_500,
        target_signal="db",
        failure_count=1,
        timeout_seconds=0.01,
        mock_base_url="http://unused",
    )
    with pytest.raises(ToolExecutionError, match="injected HTTP 500"):
        await provider.read("db", alert())
    assert await provider.read("db", alert()) == {"replication_lag_seconds": 20}

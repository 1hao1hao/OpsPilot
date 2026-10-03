from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, RootCauseType, ToolCall
from opspilot.tools import ToolExecutor, build_default_registry
from opspilot.tools.errors import ToolValidationError, UnknownToolError


def make_alert(**signals) -> AlertEvent:
    return AlertEvent(
        alert_id="contract-alert",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),
        description="read requests are timing out",
        signals=signals,
    )


def test_alert_rejects_unknown_fields_and_naive_timestamp():
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**make_alert().model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**make_alert().model_dump(), "timestamp": "2026-08-01T10:00:00"})


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool_and_invalid_input():
    executor = ToolExecutor(build_default_registry())
    with pytest.raises(UnknownToolError):
        await executor.execute(ToolCall(tool_call_id="x", tool_name="unknown", arguments={}))
    with pytest.raises(ToolValidationError):
        await executor.execute(ToolCall(tool_call_id="x", tool_name="db.connections", arguments={"bad": "input"}))


@pytest.mark.asyncio
async def test_db_and_normal_reports_are_valid_with_fake_summary():
    workflow = OpsPilotWorkflow(build_default_registry())
    db_report = await workflow.analyze(make_alert(db={"replication_lag_seconds": 20}, trace={"traces": [{"trace_id": "db", "spans": [{"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10}, {"span_id": "db", "parent_span_id": "root", "service": "mysql", "status": "ERROR", "duration_ms": 1500}]}]}))
    assert db_report.primary_root_cause.root_cause_type == RootCauseType.DB_REPLICATION_LAG
    assert "db.replication_lag" in {item.evidence_type for item in db_report.evidence}
    normal_report = await workflow.analyze(make_alert(metric={"cpu_usage": 0.5}))
    assert normal_report.primary_root_cause.root_cause_type == RootCauseType.NO_FAULT
    assert normal_report.status == "completed"


def test_agents_do_not_import_io_clients():
    from pathlib import Path

    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/opspilot/agents").glob("*.py"))
    assert "import httpx" not in source
    assert "import redis" not in source
    assert "import sqlalchemy" not in source.lower()

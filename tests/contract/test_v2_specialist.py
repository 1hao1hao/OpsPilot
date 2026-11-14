from datetime import UTC, datetime

import pytest

from opspilot.investigation.specialist import SpecialistAgent, SpecialistContext
from opspilot.investigation.tasks import InvestigationTask, TaskStatus
from opspilot.models import AlertEvent, ToolResult, ToolStatus
from opspilot.tools.registry import build_default_registry


def context(tools):
    return SpecialistContext(
        alert=AlertEvent(alert_id="specialist", service_name="checkout", alert_type="timeout",
                         severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
                         signals={"db": {"replication_lag_seconds": 21}}),
        task=InvestigationTask(
            task_id="db-lag", objective="measure replication lag", target_service="checkout",
            domain="db", candidate_cause="db_replication_lag", required_signal="replication_lag",
            priority=100, assigned_agent="db", allowed_tools=tools, status=TaskStatus.READY,
        ),
        round_number=2,
    )


@pytest.mark.asyncio
async def test_specialist_reassesses_failed_measurement_and_uses_only_authorized_alternative():
    calls = []

    async def run(name, round_number, reason, target):
        calls.append((name, round_number, target))
        if name == "db.connections":
            return ToolResult(tool_call_id="first", tool_name=name, status=ToolStatus.ERROR,
                              error_code="timeout", latency_ms=1)
        return ToolResult(tool_call_id="second", tool_name=name, status=ToolStatus.SUCCESS,
                          data={"observations": {"replication_lag_seconds": 21}}, latency_ms=1)

    agent = SpecialistAgent(build_default_registry())
    result = await agent.investigate(context(["db.connections", "db.replication"]), run)
    assert calls == [("db.connections", 2, "checkout"), ("db.replication", 2, "checkout")]
    assert result.tool_calls == 2
    assert result.supporting_evidence
    assert not result.unresolved_questions


@pytest.mark.asyncio
async def test_specialist_preserves_unresolved_task_on_failed_measurement():
    async def fail(name, round_number, reason, target):
        return ToolResult(tool_call_id="failed", tool_name=name, status=ToolStatus.ERROR,
                          error_code="timeout", latency_ms=1)

    result = await SpecialistAgent(build_default_registry()).investigate(
        context(["db.replication"]), fail)
    assert result.unresolved_questions == ["measure replication lag"]
    assert result.supporting_evidence == []
    assert result.tool_calls == 1


@pytest.mark.asyncio
async def test_specialist_rejects_tool_outside_task_domain():
    async def unused(*args):
        raise AssertionError("tool runner must not be called")

    with pytest.raises(ValueError, match="outside specialist domain"):
        await SpecialistAgent(build_default_registry()).investigate(
            context(["redis.memory"]), unused)

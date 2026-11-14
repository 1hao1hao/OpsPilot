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


@pytest.mark.asyncio
async def test_specialist_llm_chooses_local_tool_but_cannot_expand_whitelist():
    calls = []

    async def choose(*, system_prompt, payload):
        assert "final root cause" in system_prompt
        assert payload["allowed_tools"] == ["db.connections", "db.replication"]
        return {"action": "inspect", "tool_name": "db.replication", "reason": "measure replica lag"}

    async def run(name, *_args):
        calls.append(name)
        return ToolResult(tool_call_id="llm-choice", tool_name=name, status=ToolStatus.SUCCESS,
                          data={"observations": {"replication_lag_seconds": 21}}, latency_ms=1)

    agent = SpecialistAgent(build_default_registry(), json_call=choose)
    result = await agent.investigate(context(["db.connections", "db.replication"]), run)
    assert calls == ["db.replication"]
    assert result.llm_used
    assert result.local_decisions[0].tool_name == "db.replication"

    async def unauthorized(**_kwargs):
        return {"action": "inspect", "tool_name": "redis.memory", "reason": "try cache"}

    fallback_agent = SpecialistAgent(build_default_registry(), json_call=unauthorized)
    fallback = await fallback_agent.investigate(context(["db.connections"]), run)
    assert calls[-1] == "db.connections"
    assert not fallback.llm_used
    assert "whitelist" in fallback.fallback_reason


@pytest.mark.asyncio
async def test_specialist_token_budget_falls_back_and_records_actual_usage():
    calls = 0

    async def choose(**_kwargs):
        nonlocal calls
        calls += 1
        return {"decision": {"action": "inspect", "tool_name": "db.replication",
                             "reason": "verify lag"}, "token_usage": 137}

    async def run(name, *_args):
        return ToolResult(tool_call_id="budgeted", tool_name=name, status=ToolStatus.SUCCESS,
                          data={"observations": {"replication_lag_seconds": 21}}, latency_ms=1)

    agent = SpecialistAgent(build_default_registry(), json_call=choose)
    no_tokens = context(["db.replication"]).model_copy(update={"token_budget": 0})
    fallback = await agent.investigate(no_tokens, run)
    assert calls == 0 and fallback.token_usage == 0
    assert "token budget" in fallback.fallback_reason
    budgeted = context(["db.replication"]).model_copy(update={"token_budget": 2000})
    result = await agent.investigate(budgeted, run)
    assert calls == 1 and result.llm_used and result.token_usage == 137

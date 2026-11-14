import asyncio
from datetime import UTC, datetime

from opspilot.config import RuntimeSettings
from opspilot.graph.workflow import OpsPilotWorkflow
from opspilot.investigation.v2_graph import GraphInvestigator
from opspilot.models import AlertEvent, ToolCall, ToolResult, ToolStatus
from opspilot.tools import ToolExecutor
from opspilot.tools.registry import build_default_registry


async def test_langgraph_dispatches_and_merges_scoped_specialist_results():
    registry = build_default_registry()
    alert = AlertEvent(
        alert_id="graph-db", service_name="checkout", alert_type="timeout", severity="P1",
        timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        signals={"metric": {"tp95": {"current": 1200, "baseline": 100}},
                 "trace": {"traces": [{"trace_id": "db-path", "spans": [
                     {"span_id": "root", "service": "checkout", "status": "OK"},
                     {"span_id": "db", "parent_span_id": "root", "service": "mysql",
                      "status": "ERROR", "duration_ms": 1400}]}]},
                 "db": {"replication_lag_seconds": 21}},
    )
    executor = ToolExecutor(registry)
    active_specialists = 0
    peak_specialists = 0

    async def run_tool(name, round_number, _reason, target):
        nonlocal active_specialists, peak_specialists
        if round_number > 1:
            active_specialists += 1
            peak_specialists = max(peak_specialists, active_specialists)
            await asyncio.sleep(0.02)
            active_specialists -= 1
        return await executor.execute(ToolCall(
            tool_call_id=f"graph:{round_number}:{name}:{target}", tool_name=name,
            arguments={"alert": alert.model_dump(mode="json"), "target_service": target}))

    graph = GraphInvestigator(registry, settings=RuntimeSettings(
        investigation_max_rounds=3, investigation_max_tool_calls=10))
    outcome = await graph.run(alert, run_tool)
    assert outcome.trace.rounds >= 1
    assert outcome.state["task_graph"]["version"] >= 1
    assert len(outcome.state["specialist_results"]) >= 2
    assert peak_specialists >= 2
    assert outcome.state["evidence_board"]
    assert all(item["tool_call_id"] in {result.tool_call_id for result in outcome.tool_results}
               for item in outcome.state["evidence_board"])
    assert outcome.trace.decision is not None
    assert len(outcome.trace.executed_tool_targets) == len(set(outcome.trace.executed_tool_targets))
    no_experts = GraphInvestigator(registry, settings=RuntimeSettings(
        investigation_max_rounds=3, investigation_max_tool_calls=10,
        investigation_max_expert_calls=0))
    restricted = await no_experts.run(alert, run_tool)
    assert restricted.trace.expert_budget_used == 0
    assert not any(item["task_id"].startswith("db:") for item in restricted.state["specialist_results"])


async def test_concurrent_graph_runs_do_not_share_tool_runner_context():
    graph = GraphInvestigator(build_default_registry(), settings=RuntimeSettings(investigation_max_rounds=1))

    async def one(identity):
        alert = AlertEvent(alert_id=identity, service_name="checkout", alert_type="timeout",
                           severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC))

        async def run_tool(name, _round, _reason, _target):
            await asyncio.sleep(0)
            return ToolResult(tool_call_id=f"{identity}:{name}", tool_name=name,
                              status=ToolStatus.SUCCESS, data={"observations": {}}, latency_ms=0)

        return await graph.run(alert, run_tool)

    left, right = await asyncio.gather(one("left"), one("right"))
    assert all(item.tool_call_id.startswith("left:") for item in left.tool_results)
    assert all(item.tool_call_id.startswith("right:") for item in right.tool_results)


async def test_v2_mode_runs_through_public_offline_workflow():
    alert = AlertEvent(alert_id="v2-workflow", service_name="checkout", alert_type="timeout",
                       severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
                       signals={"metric": {"cpu_usage": 0.82}})
    workflow = OpsPilotWorkflow(build_default_registry(), settings=RuntimeSettings(
        investigation_mode="v2", investigation_max_rounds=2))
    report = await workflow.analyze(alert, trace_id="v2-public")
    assert report.trace_id == "v2-public"
    assert report.investigation is not None
    assert report.investigation.decision is not None

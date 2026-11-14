import asyncio
from datetime import UTC, datetime, timedelta

from opspilot.config import RuntimeSettings
from opspilot.graph.workflow import OpsPilotWorkflow
from opspilot.investigation.v2_graph import GraphInvestigator
from opspilot.models import AlertEvent, ToolCall, ToolResult, ToolStatus
from opspilot.skills import SkillCatalog
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
    snapshots = []

    async def capture(event, snapshot):
        if event == "investigation.gate":
            snapshots.append(snapshot)

    outcome = await graph.run(alert, run_tool, on_state=capture)
    assert outcome.trace.rounds >= 1
    assert outcome.state["task_graph"]["version"] >= 1
    assert len(outcome.state["specialist_results"]) >= 2
    assert peak_specialists >= 2
    assert len(snapshots) >= 2
    assert snapshots[1]["task_graph"]["version"] > snapshots[0]["task_graph"]["version"]
    assert len(snapshots[1]["tool_results"]) > len(snapshots[0]["tool_results"])
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


async def test_expired_graph_time_budget_keeps_unconfirmed_decision():
    alert = AlertEvent(alert_id="expired", service_name="checkout", alert_type="timeout",
                       severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC))
    graph = GraphInvestigator(build_default_registry(), settings=RuntimeSettings(
        investigation_max_seconds=0.01))

    async def unused(*_args):
        raise AssertionError("expired budget must not start a tool")

    outcome = await graph.run(alert, unused, restored_state={
        "started_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
    assert outcome.trace.decision.verdict.value == "INCONCLUSIVE"
    assert "time budget exhausted" in outcome.trace.stop_reason
    assert not outcome.tool_results


async def test_skills_change_real_specialist_fallback_tool_after_failure():
    registry = build_default_registry()
    alert = AlertEvent(
        alert_id="skill-fallback", service_name="checkout", alert_type="timeout", severity="P1",
        timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        signals={"metric": {"tp95": {"current": 1200, "baseline": 100}},
                 "trace": {"traces": [{"trace_id": "slow-db", "spans": [
                     {"span_id": "root", "service": "checkout", "status": "OK"},
                     {"span_id": "db", "parent_span_id": "root", "service": "mysql",
                      "status": "ERROR", "duration_ms": 1400}]}]},
                 "db": {"slow_query_count": 50, "active_connections": 20, "max_connections": 100}},
    )
    executor = ToolExecutor(registry)

    async def run_tool(name, round_number, _reason, target):
        if name == "db.connections":
            return ToolResult(tool_call_id=f"failed:{round_number}:{name}", tool_name=name,
                              status=ToolStatus.ERROR, error_code="backend_timeout", latency_ms=1)
        return await executor.execute(ToolCall(
            tool_call_id=f"read:{round_number}:{name}:{target}", tool_name=name,
            arguments={"alert": alert.model_dump(mode="json"), "target_service": target}))

    settings = RuntimeSettings(investigation_max_rounds=2, investigation_max_tool_calls=10)
    plain = await GraphInvestigator(registry, settings=settings).run(alert, run_tool)
    skilled = await GraphInvestigator(registry, settings=settings, skills=SkillCatalog()).run(alert, run_tool)
    plain_tools = [item.tool_name for item in plain.tool_results]
    skilled_tools = [item.tool_name for item in skilled.tool_results]
    assert "db.connections" in plain_tools and "db.connections" in skilled_tools
    assert "db.replication" in plain_tools
    assert "db.slowlog" in skilled_tools
    assert plain_tools != skilled_tools
    assert skilled.trace.decision.verdict.value == "INCONCLUSIVE"
    assert any(item.active and not item.satisfied for item in skilled.trace.decision.requirements)

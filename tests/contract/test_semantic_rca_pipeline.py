from datetime import UTC, datetime

import pytest

from opspilot.agents import RootCauseAgent
from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, InvestigationActionType, RootCauseType
from opspilot.tools import build_default_registry


def rich_alert() -> AlertEvent:
    baseline = [100.0] * 20
    return AlertEvent(
        alert_id="semantic-timeout",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        description="payment dependency timeout",
        signals={
            "metric": {
                "tp99": {
                    "data_points": [{"value": value} for value in [*baseline, 500.0]],
                    "current": 500.0,
                    "last_week": 100.0,
                    "yesterday": 100.0,
                },
                "cpu_usage": 0.9,
            },
            "trace": {"traces": [{
                "trace_id": "trace-semantic",
                "spans": [
                    {"span_id": "root", "service": "order-service", "duration_ms": 20, "status": "OK"},
                    {"span_id": "payment", "parent_span_id": "root", "service": "payment-service", "duration_ms": 40, "status": "OK"},
                    {"span_id": "redis", "parent_span_id": "payment", "service": "redis", "duration_ms": 1300, "status": "TIMEOUT"},
                ],
            }]},
            "change": {},
        },
    )


@pytest.mark.asyncio
async def test_online_workflow_runs_seed_gate_l3_and_full_span_path_analysis():
    report = await OpsPilotWorkflow(build_default_registry()).analyze(rich_alert())

    assert [item.dimension for item in report.dimension_results] == ["upstream", "downstream", "change"]
    assert {item.dimension for item in report.expert_results} == {"redis", "rpc"}
    assert {item.algorithm for item in report.algorithm_signals} >= {
        "metric_filter+noise_filter", "wow_dod_comparator", "iqr_detector", "volatility_detector"
    }
    trace_evidence = next(item for item in report.evidence if item.evidence_type == "downstream.downstream_span_error")
    assert trace_evidence.service == "redis"
    assert "order-service/payment-service/redis" in trace_evidence.fact
    assert trace_evidence.raw_ref == "trace:trace-semantic/span:redis"
    assert report.primary_root_cause.root_cause_type == RootCauseType.RPC_TIMEOUT
    assert report.investigation.rounds == 3
    assert report.investigation.gate_decisions[0].sufficient is False


@pytest.mark.parametrize(
    ("alert_type", "expected_tools"),
    [
        ("timeout", ["metrics.query", "traces.query", "changes.query"]),
        ("error_rate", ["metrics.query", "logs.query", "traces.query"]),
        ("resource", ["metrics.query", "logs.query"]),
    ],
)
def test_seed_planner_is_alert_aware_bounded_and_has_no_domain_tools(alert_type, expected_tools):
    alert = AlertEvent(
        alert_id=f"seed-{alert_type}", service_name="order-service", alert_type=alert_type,
        severity="P2", timestamp=datetime(2026, 8, 29, tzinfo=UTC),
    )
    plan = OpsPilotWorkflow(build_default_registry()).coordinator.plan(alert)

    assert [item.tool_name for item in plan.steps] == expected_tools
    assert 2 <= len(plan.steps) <= 3
    assert not any(name.startswith(("db.", "redis.", "kafka.", "rpc.")) for name in expected_tools)
    assert all(not item.expert_domains for item in plan.dimensions)


@pytest.mark.asyncio
async def test_evidence_shortage_triggers_db_expert_after_seed_observation():
    alert = AlertEvent(
        alert_id="adaptive-db", service_name="payment-service", alert_type="timeout", severity="P1",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC), description="request timeout",
        signals={
            "trace": {"traces": [{"trace_id": "t", "spans": [
                {"span_id": "root", "service": "payment-service", "duration_ms": 30, "status": "OK"},
                {"span_id": "mysql", "parent_span_id": "root", "service": "mysql", "duration_ms": 1500, "status": "ERROR"},
            ]}]},
            "db": {"replication_lag_seconds": 15},
        },
    )
    report = await OpsPilotWorkflow(build_default_registry()).analyze(alert)
    trace = report.investigation

    assert trace.executed_tools[:3] == ["metrics.query", "traces.query", "changes.query"]
    assert trace.invoked_experts == ["db"]
    assert "db.replication" in trace.executed_tools
    expert_action = next(item for item in trace.action_history if item.action_type == InvestigationActionType.INVOKE_EXPERT)
    assert expert_action.round == 2
    assert "observed context matched db" in expert_action.reason
    assert report.primary_root_cause.root_cause_type == RootCauseType.DB_REPLICATION_LAG
    assert trace.duplicate_actions == 0
    assert trace.tool_budget_used <= 8


@pytest.mark.asyncio
async def test_resource_alert_activates_only_the_observed_db_expert():
    alert = AlertEvent(
        alert_id="resource-db", service_name="payment-service", alert_type="resource", severity="P1",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        signals={"db": {"active_connections": 196, "max_connections": 200}},
    )
    report = await OpsPilotWorkflow(build_default_registry()).analyze(alert)

    assert report.investigation.executed_tools[:2] == ["metrics.query", "logs.query"]
    assert report.investigation.invoked_experts == ["db"]
    assert "db.connections" in report.investigation.executed_tools
    assert [item.dimension for item in report.expert_results] == ["db"]
    assert report.primary_root_cause.root_cause_type == RootCauseType.DB_CONNECTION_EXHAUSTED


def test_labels_and_severity_reorder_seed_without_expanding_it():
    alert = AlertEvent(
        alert_id="labeled-custom", service_name="payment-service", alert_type="custom", severity="P0",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        labels={"component": "payment-db", "change_kind": "release"},
    )
    plan = OpsPilotWorkflow(build_default_registry()).coordinator.plan(alert)

    assert [item.tool_name for item in plan.steps] == ["changes.query", "metrics.query", "logs.query"]
    assert all(not item.expert_domains for item in plan.dimensions)


@pytest.mark.asyncio
async def test_optional_llm_explains_but_cannot_change_deterministic_candidate():
    async def summarize(_alert, candidate, _evidence):
        return f"LLM explanation constrained to {candidate.root_cause_type.value}"

    workflow = OpsPilotWorkflow(
        build_default_registry(), root_cause_agent=RootCauseAgent(async_summarizer=summarize),
    )
    report = await workflow.analyze(rich_alert())

    assert report.primary_root_cause.root_cause_type == RootCauseType.RPC_TIMEOUT
    assert report.llm_used is True
    assert report.decision_rationale == "LLM explanation constrained to rpc_timeout"

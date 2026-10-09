from datetime import UTC, datetime

import pytest

from opspilot.agents import CoordinatorAgent, RootCauseAgent
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

    assert not {"dimension_results", "expert_results", "algorithm_signals", "matched_rules", "tool_executions"} & report.model_dump().keys()
    assert {item.evidence_type for item in report.evidence} >= {"anomaly.historical_baseline", "anomaly.iqr", "anomaly.volatility"}
    trace_evidence = next(item for item in report.evidence if item.evidence_type == "trace.span_error")
    assert trace_evidence.service == "redis"
    assert "order-service/payment-service/redis" in trace_evidence.fact
    assert trace_evidence.raw_ref == "trace:trace-semantic/span:redis"
    assert report.primary_root_cause.root_cause_type == RootCauseType.RESOURCE_SATURATION
    assert report.investigation.rounds <= 4
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
    plan = CoordinatorAgent(build_default_registry()).plan(alert)

    assert [item.tool_name for item in plan.steps] == expected_tools
    assert 2 <= len(plan.steps) <= 3
    assert not any(name.startswith(("db.", "redis.", "kafka.", "rpc.")) for name in expected_tools)
    assert "dimensions" not in plan.model_dump()


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
    # Legacy spans omit incident timestamps: investigate the observed DB first,
    # but do not finalize merely because the root ranking gate passes.
    assert trace.invoked_experts[0] == "db"
    assert report.verdict.value == "INCONCLUSIVE"
    assert "db.replication" in trace.executed_tools
    expert_action = next(item for item in trace.action_history if item.action_type == InvestigationActionType.INVOKE_EXPERT)
    assert expert_action.round == 2
    assert "Evidence supports db" in expert_action.reason
    assert report.primary_root_cause.root_cause_type == RootCauseType.DB_REPLICATION_LAG
    assert trace.duplicate_actions == 0
    assert trace.tool_budget_used <= 8


@pytest.mark.asyncio
async def test_resource_alert_activates_only_the_observed_db_expert():
    alert = AlertEvent(
        alert_id="resource-db", service_name="payment-service", alert_type="resource", severity="P1",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        signals={"db": {"active_connections": 196, "max_connections": 200},
                 "trace": {"traces": [{"trace_id": "db", "spans": [{"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10}, {"span_id": "db", "parent_span_id": "root", "service": "mysql", "status": "ERROR", "duration_ms": 1500}]}]}},
    )
    report = await OpsPilotWorkflow(build_default_registry()).analyze(alert)

    assert report.investigation.executed_tools[:2] == ["metrics.query", "logs.query"]
    assert report.investigation.invoked_experts[0] == "db"
    assert "db.connections" in report.investigation.executed_tools
    assert any(item.evidence_type == "db.connection_usage" for item in report.evidence)
    assert report.primary_root_cause.root_cause_type == RootCauseType.DB_CONNECTION_EXHAUSTED


def test_labels_and_severity_reorder_seed_without_expanding_it():
    alert = AlertEvent(
        alert_id="labeled-custom", service_name="payment-service", alert_type="custom", severity="P0",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        labels={"component": "payment-db", "change_kind": "release"},
    )
    plan = CoordinatorAgent(build_default_registry()).plan(alert)

    assert [item.tool_name for item in plan.steps] == ["changes.query", "metrics.query", "logs.query"]
    assert "dimensions" not in plan.model_dump()


@pytest.mark.asyncio
async def test_optional_llm_explains_but_cannot_change_deterministic_candidate():
    async def summarize(_alert, candidate, _evidence):
        return f"LLM explanation constrained to {candidate.root_cause_type.value}"

    workflow = OpsPilotWorkflow(
        build_default_registry(), root_cause_agent=RootCauseAgent(async_summarizer=summarize),
    )
    report = await workflow.analyze(rich_alert())

    assert report.primary_root_cause.root_cause_type == RootCauseType.RESOURCE_SATURATION
    assert report.llm_used is True
    assert "provisional ranking: LLM explanation constrained to resource_saturation" in report.decision_rationale

from datetime import UTC, datetime

import pytest

from opspilot.agents import RootCauseAgent
from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, RootCauseType
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
            "trace": {
                "traces": [
                    {
                        "trace_id": "trace-semantic",
                        "spans": [
                            {"span_id": "root", "service": "order-service", "duration_ms": 20, "status": "OK"},
                            {"span_id": "payment", "parent_span_id": "root", "service": "payment-service", "duration_ms": 40, "status": "OK"},
                            {"span_id": "redis", "parent_span_id": "payment", "service": "redis", "duration_ms": 1300, "status": "TIMEOUT"},
                        ],
                    }
                ]
            },
            "log": {"logs": [{"message": "request timeout"}]},
            "change": {},
            "topology": {"downstream": [{"service": "payment-service"}]},
            "db": {},
            "redis": {},
            "kafka": {},
            "rpc": {},
            "problem": {},
        },
    )


@pytest.mark.asyncio
async def test_online_workflow_executes_semantic_expert_and_algorithm_layers():
    report = await OpsPilotWorkflow(build_default_registry()).analyze(rich_alert())

    assert [item.dimension for item in report.dimension_results] == [
        "change", "downstream", "cluster", "errorlog", "upstream", "problem"
    ]
    assert [item.dimension for item in report.expert_results] == ["db", "redis", "kafka", "rpc"]
    assert {item.algorithm for item in report.algorithm_signals} >= {
        "metric_filter+noise_filter", "wow_dod_comparator", "iqr_detector", "volatility_detector"
    }
    trace_evidence = next(item for item in report.evidence if item.evidence_type == "downstream.downstream_span_error")
    assert trace_evidence.service == "redis"
    assert "order-service/payment-service/redis" in trace_evidence.fact
    assert trace_evidence.raw_ref == "trace:trace-semantic/span:redis"
    assert report.primary_root_cause.root_cause_type == RootCauseType.RPC_TIMEOUT


def test_planner_restores_six_dimensions_and_l2_expert_domains():
    workflow = OpsPilotWorkflow(build_default_registry())
    plan = workflow.coordinator.plan(rich_alert())

    assert len(plan.dimensions) == 6
    downstream = next(item for item in plan.dimensions if item.dimension == "downstream")
    assert downstream.expert_domains == ["db", "redis", "kafka", "rpc"]
    assert "alerts.query" in {item.tool_name for item in plan.steps}


@pytest.mark.asyncio
async def test_resource_alert_does_not_prune_domain_experts():
    alert = AlertEvent(
        alert_id="resource-db",
        service_name="payment-service",
        alert_type="resource",
        severity="P1",
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        signals={"db": {"active_connections": 196, "max_connections": 200}},
    )
    workflow = OpsPilotWorkflow(build_default_registry())
    report = await workflow.analyze(alert)

    assert len(workflow.coordinator.plan(alert).dimensions) == 6
    assert report.primary_root_cause.root_cause_type == RootCauseType.DB_CONNECTION_EXHAUSTED
    assert "db.connection_usage" in {item.evidence_type for item in report.evidence}


@pytest.mark.asyncio
async def test_optional_llm_explains_but_cannot_change_deterministic_candidate():
    async def summarize(_alert, candidate, _evidence):
        return f"LLM explanation constrained to {candidate.root_cause_type.value}"

    workflow = OpsPilotWorkflow(
        build_default_registry(),
        root_cause_agent=RootCauseAgent(async_summarizer=summarize),
    )
    report = await workflow.analyze(rich_alert())

    assert report.primary_root_cause.root_cause_type == RootCauseType.RPC_TIMEOUT
    assert report.llm_used is True
    assert report.decision_rationale == "LLM explanation constrained to rpc_timeout"

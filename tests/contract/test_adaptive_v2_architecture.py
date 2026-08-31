from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from opspilot.agents import CoordinatorAgent, RootCauseAgent, analyze_experts
from opspilot.evidence import collect_evidence, collect_expert_evidence
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation.engine import AdaptiveInvestigator
from opspilot.investigation.planner import ActionValidator, EvidenceGate, LLMAdaptivePlanner
from opspilot.models import (
    AlertEvent,
    AnalysisPlan,
    Evidence,
    EvidenceSeverity,
    EvidenceSourceType,
    InvestigationAction,
    InvestigationActionType,
    RootCauseType,
    ToolResult,
    ToolStatus,
)
from opspilot.tools import build_default_registry


def alert(*, signals: dict | None = None) -> AlertEvent:
    return AlertEvent(
        alert_id="adaptive-v2",
        service_name="order-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 31, tzinfo=UTC),
        description="downstream latency is elevated",
        labels={"env": "test"},
        signals=signals or {},
    )


def planner_kwargs(item: AlertEvent) -> dict:
    return {
        "alert": item,
        "round_number": 2,
        "dimension_results": [],
        "expert_results": [],
        "evidence": [],
        "candidates": [],
        "executed_tools": ["metrics.query"],
        "invoked_experts": [],
        "action_history": [],
        "action_identities": set(),
        "remaining_round_budget": 2,
        "remaining_tool_budget": 4,
        "remaining_expert_budget": 2,
    }


@pytest.mark.asyncio
async def test_llm_planner_payload_never_contains_alert_signals_and_invalid_domain_tool_falls_back():
    captured: dict = {}

    async def illegal_output(**kwargs):
        captured.update(kwargs["payload"])
        return {"action": "inspect_tool", "target": "db.replication", "reason": "try direct access"}

    planner = LLMAdaptivePlanner(build_default_registry(), llm_enabled=True, json_call=illegal_output)
    action = await planner.decide(
        **planner_kwargs(alert(signals={"secret_backend_snapshot": {"value": "DO_NOT_LEAK"}}))
    )

    assert "DO_NOT_LEAK" not in json.dumps(captured)
    assert action is not None
    assert action.target != "db.replication"
    assert planner.last_used_llm is False
    assert "cannot inspect non-general" in (planner.last_fallback_reason or "")


@pytest.mark.asyncio
async def test_invalid_llm_shape_uses_deterministic_fallback():
    async def invalid_output(**_kwargs):
        return {"action": "FINALIZE", "target": "", "reason": "stop"}

    planner = LLMAdaptivePlanner(build_default_registry(), llm_enabled=True, json_call=invalid_output)
    action = await planner.decide(**planner_kwargs(alert()))
    assert action is not None
    assert action.action_type in {
        InvestigationActionType.INSPECT_TOOL,
        InvestigationActionType.INVOKE_EXPERT,
    }
    assert planner.last_fallback_reason


def test_central_validator_enforces_seed_planner_and_expert_tool_boundaries():
    registry = build_default_registry()
    validator = ActionValidator(registry)
    with pytest.raises(ValueError, match="non-general"):
        validator.validate_seed_tool("db.replication")
    with pytest.raises(ValueError, match="cannot inspect non-general"):
        validator.validate_planner_action(
            InvestigationAction(
                action_type=InvestigationActionType.INSPECT_TOOL,
                target="redis.memory",
                reason="illegal direct call",
                round=2,
            )
        )
    with pytest.raises(ValueError, match="db Expert cannot execute"):
        validator.validate_expert_tool("db", "redis.memory")


def test_derived_evidence_from_one_tool_result_counts_as_one_source():
    item = alert()
    result = ToolResult(
        tool_call_id="db-observation-1",
        tool_name="db.replication",
        status=ToolStatus.SUCCESS,
        data={"observations": {"replication_lag_seconds": 20}},
        latency_ms=1,
    )
    expert_results = analyze_experts(item, [result], domains=["db"])
    evidence = collect_evidence(item, [result]) + collect_expert_evidence(item, expert_results)
    candidates, _ = RootCauseAgent().diagnose(item, evidence)
    decision = EvidenceGate(confidence=0.7, margin=0.1, min_sources=2).evaluate(candidates, evidence)

    supporting = [entry for entry in evidence if RootCauseType.DB_REPLICATION_LAG in entry.supports]
    assert len(supporting) >= 2
    assert len({entry.source_group for entry in supporting}) == 1
    assert decision.independent_source_count == 1
    assert decision.sufficient is False


def test_distinct_observations_count_as_multiple_sources():
    item = alert()
    evidence = [
        Evidence(
            evidence_id=f"ev-{index}",
            evidence_type="trace.db_path" if index == 1 else "db.replication_lag",
            source_type=EvidenceSourceType.TRACE if index == 1 else EvidenceSourceType.METRIC,
            source_name="traces.query" if index == 1 else "db.replication",
            source_group=f"tool:observation:{index}",
            service="mysql",
            observed_at=item.timestamp,
            fact="independent support",
            severity=EvidenceSeverity.CRITICAL,
            confidence=0.9,
            supports=[RootCauseType.DB_REPLICATION_LAG],
        )
        for index in (1, 2)
    ]
    candidates, _ = RootCauseAgent().diagnose(item, evidence)
    decision = EvidenceGate(confidence=0.7, margin=0.1, min_sources=2).evaluate(candidates, evidence)
    assert decision.independent_source_count == 2
    assert decision.sufficient is True


@pytest.mark.asyncio
async def test_each_gate_sees_algorithm_evidence_and_final_report_does_not_rerun_analysis():
    item = alert(
        signals={
            "metric": {
                "tp99": {
                    "data_points": [
                        *({"value": 100 + index % 3} for index in range(20)),
                        {"value": 1100},
                    ]
                }
            }
        }
    )
    workflow = OpsPilotWorkflow(build_default_registry())
    seen_types: list[set[str]] = []
    original_evaluate = workflow.investigator.gate.evaluate

    def capture(candidates, evidence, *, budget_exhausted=False):
        seen_types.append({entry.evidence_type for entry in evidence})
        return original_evaluate(candidates, evidence, budget_exhausted=budget_exhausted)

    workflow.investigator.gate.evaluate = capture
    report = await workflow.analyze(item)

    assert seen_types
    assert all(any(name.startswith("algorithm.") for name in types) for types in seen_types)
    assert workflow.investigator.analysis_engine.analysis_count == report.investigation.rounds
    assert seen_types[-1] == {entry.evidence_type for entry in report.evidence}


def test_coordinator_seed_contains_general_tools_only():
    registry = build_default_registry()
    plan = CoordinatorAgent(registry).plan(alert())
    assert {step.tool_name for step in plan.steps} <= set(registry.general_names())


@pytest.mark.parametrize(
    ("alert_type", "tool_name", "expected_dimension"),
    [
        ("timeout", "metrics.query", "upstream"),
        ("error_rate", "metrics.query", "upstream"),
        ("resource", "metrics.query", "cluster"),
        ("custom", "metrics.query", "cluster"),
        ("timeout", "logs.query", "errorlog"),
        ("timeout", "changes.query", "change"),
        ("timeout", "traces.query", "downstream"),
        ("timeout", "topology.query", "upstream"),
        ("timeout", "alerts.query", "problem"),
    ],
)
def test_dynamic_analysis_plan_maps_every_general_tool(
    alert_type: str,
    tool_name: str,
    expected_dimension: str,
):
    item = AlertEvent.model_validate({**alert().model_dump(), "alert_type": alert_type})
    plan = AdaptiveInvestigator._analysis_plan(item, AnalysisPlan(steps=[]), [tool_name])

    assert [(dimension.dimension, dimension.tools) for dimension in plan.dimensions] == [
        (expected_dimension, [tool_name])
    ]


def test_dynamic_analysis_plan_merges_tools_that_share_upstream_dimension():
    item = alert()
    seed = CoordinatorAgent(build_default_registry()).plan(item)
    plan = AdaptiveInvestigator._analysis_plan(
        item,
        seed,
        ["metrics.query", "traces.query", "changes.query", "topology.query"],
    )

    upstream = next(dimension for dimension in plan.dimensions if dimension.dimension == "upstream")
    assert upstream.tools == ["metrics.query", "topology.query"]

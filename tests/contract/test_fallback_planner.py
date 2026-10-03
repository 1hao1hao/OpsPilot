from datetime import UTC, datetime

import pytest

from opspilot.evidence import collect_evidence
from opspilot.investigation.planner import ActionValidator, DeterministicPlannerFallback
from opspilot.models import AlertEvent, ToolResult
from opspilot.tools import build_default_registry


def context():
    return {
        "alert": AlertEvent(
            alert_id="fallback",
            service_name="mysql",
            alert_type="timeout",
            severity="P1",
            timestamp=datetime(2026, 10, 3, tzinfo=UTC),
            description="redis kafka database timeout",
            signals={"db": {"replication_lag_seconds": 50}},
        ),
        "round_number": 2,
        "evidence": [],
        "candidates": [],
        "executed_tools": ["metrics.query"],
        "invoked_experts": [],
        "action_history": [],
        "action_identities": set(),
        "remaining_round_budget": 3,
        "remaining_tool_budget": 6,
        "remaining_expert_budget": 2,
    }


def decide(**updates):
    registry = build_default_registry()
    kwargs = {**context(), **updates}
    return DeterministicPlannerFallback(registry, ActionValidator(registry)).decide(**kwargs)


def test_fallback_ignores_keywords_severity_and_backend_snapshots():
    action = decide()
    assert action.action_type.value == "inspect_tool"
    assert action.target == "logs.query"


@pytest.mark.parametrize(
    "tool,observations,domain",
    [
        ("db.replication", {"replication_lag_seconds": 20}, "db"),
        ("redis.memory", {"memory_usage_percent": 95}, "redis"),
        ("kafka.lag", {"consumer_lag": 10000}, "kafka"),
        ("rpc.metrics", {"timeout_rate": 0.2}, "rpc"),
    ],
)
def test_evidence_points_to_uninvoked_expert(tool, observations, domain):
    evidence = collect_evidence(
        context()["alert"],
        [
            ToolResult(
                tool_call_id="observation",
                tool_name=tool,
                status="success",
                latency_ms=1,
                data={"observations": observations},
            )
        ],
    )
    action = decide(evidence=evidence)
    assert (action.action_type.value, action.target) == ("invoke_expert", domain)
    assert decide(evidence=evidence, invoked_experts=[domain]).target == "logs.query"
    assert decide(evidence=evidence, remaining_expert_budget=0).target == "logs.query"
    assert decide(evidence=evidence, action_identities={action.identity}).target == "logs.query"


def test_no_available_action_returns_none():
    assert decide(executed_tools=build_default_registry().general_names()) is None
    assert decide(remaining_round_budget=0) is None
    assert decide(remaining_tool_budget=0) is None

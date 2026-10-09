from datetime import UTC, datetime

import pytest

from opspilot.agents import RootCauseAgent
from opspilot.config import RuntimeSettings
from opspilot.evidence import collect_evidence
from opspilot.investigation import AdaptiveInvestigator, DeterministicEvidenceEngine
from opspilot.investigation.planner import EvidenceGate, LLMAdaptivePlanner
from opspilot.models import AlertEvent, RootCauseType, ToolResult
from opspilot.rca import AnomalyDetector
from opspilot.tools import build_default_registry


def alert():
    return AlertEvent(
        alert_id="direct",
        service_name="checkout",
        alert_type="resource",
        severity="P2",
        timestamp=datetime(2026, 10, 3, tzinfo=UTC),
    )


def result(name, observations):
    return ToolResult(
        tool_call_id=f"call-{name}", tool_name=name, status="success", data={"observations": observations}, latency_ms=1
    )


@pytest.mark.parametrize(
    ("name", "observations", "cause"),
    [
        ("metrics.query", {"cpu_usage": {"data_points": [{"value": 92}]}}, "resource_saturation"),
        ("logs.query", {"logs": [{"message": "OOMKilled"}]}, "oom_restart"),
        ("changes.query", {"changes": [{"version": "v2"}]}, "bad_deployment"),
        ("traces.query", {"timeout_rate": 0.2}, "rpc_timeout"),
        ("db.replication", {"replication_lag_seconds": 20}, "db_replication_lag"),
        ("db.slowlog", {"slow_query_count": 50}, "db_slow_query"),
        ("db.connections", {"active_connections": {"current": 90, "max": 100}}, "db_connection_exhausted"),
        ("redis.memory", {"used_memory": {"usage_ratio": 0.95}}, "redis_memory_pressure"),
        ("redis.hotkeys", {"hit_rate": {"current": 0.5}}, "redis_low_hit_rate"),
        ("kafka.lag", {"total_lag": 50000}, "kafka_consumer_lag"),
        ("rpc.metrics", {"error_rate": 0.2}, "rpc_error_rate"),
    ],
)
def test_tool_results_directly_enter_the_same_pool(name, observations, cause):
    observation = result(name, observations)
    analysis = DeterministicEvidenceEngine().analyze(alert(), [observation, observation])
    supporting = [item for item in analysis.evidence if RootCauseType(cause) in item.supports]
    assert len(supporting) == 1
    assert supporting[0].source_group == f"tool:{name}:{observation.tool_call_id}"
    assert supporting[0].raw_ref == observation.tool_call_id
    assert RootCauseType(cause) in [item.root_cause_type for item in analysis.candidates]


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ({"current": 500, "last_week": 100}, "historical_baseline"),
        ({"current": 500, "yesterday": 100}, "historical_baseline"),
        ({"current": 500, "aggregation": {"baseline": 100}}, "historical_baseline"),
        ({"current": 500, "baseline_series": [99, 100, 101] * 10}, "iqr"),
        ({"time_series": [100] * 30 + [100, 500] * 5}, "volatility"),
        ({"time_series": [100] * 29 + [500]}, "volatility"),
    ],
)
def test_anomaly_methods_produce_evidence(payload, kind):
    evidence = AnomalyDetector().detect(alert(), [result("metrics.query", {"tp99": payload})])
    assert f"anomaly.{kind}" in {item.evidence_type for item in evidence}
    assert all(item.source_group == "tool:metrics.query:call-metrics.query" for item in evidence)


def test_stable_series_does_not_create_volatility_evidence():
    evidence = AnomalyDetector().detect(
        alert(),
        [
            result(
                "metrics.query",
                {
                    "tp99": {
                        "time_series": [99, 100, 101, 100] * 10,
                    }
                },
            )
        ],
    )
    assert evidence == []


def test_algorithms_cannot_inflate_independent_source_count():
    analysis = DeterministicEvidenceEngine().analyze(
        alert(),
        [
            result(
                "metrics.query",
                {
                    "tp99": {
                        "current": 500,
                        "last_week": 100,
                        "data_points": [100] * 29 + [500],
                    }
                },
            )
        ],
    )
    assert len(analysis.evidence) == 3
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=2).evaluate(analysis.candidates, analysis.evidence)
    assert gate.independent_source_count == 1
    assert not gate.sufficient


def test_ranking_subtracts_contradictions_and_is_order_independent():
    evidence = collect_evidence(
        alert(), [result("metrics.query", {"cpu_usage": 0.95}), result("logs.query", {"messages": ["OOMKilled"]})]
    )
    contradiction = evidence[0].model_copy(
        update={"evidence_id": "negative", "supports": [], "contradicts": [RootCauseType.OOM_RESTART]}
    )
    ranker = RootCauseAgent()
    candidates, _ = ranker.diagnose(alert(), [*evidence, contradiction])
    reversed_candidates, _ = ranker.diagnose(alert(), [contradiction, *reversed(evidence)])
    assert candidates == reversed_candidates
    assert candidates[0].root_cause_type == RootCauseType.RESOURCE_SATURATION


@pytest.mark.asyncio
async def test_gate_pass_never_calls_planner():
    async def forbidden(**kwargs):
        pytest.fail("Planner called after sufficient evidence")

    registry = build_default_registry()
    planner = LLMAdaptivePlanner(registry, llm_enabled=True, json_call=forbidden)
    investigator = AdaptiveInvestigator(
        registry, planner=planner, settings=RuntimeSettings(evidence_gate_min_sources=1)
    )

    async def execute(name, *_):
        return result(name, {"cpu_usage": 0.99} if name == "metrics.query" else {})

    outcome = await investigator.run(alert(), execute)
    assert outcome.trace.rounds == 1
    assert outcome.trace.gate_decisions[-1].sufficient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,target,tool,observations,cause",
    [
        ("inspect_tool", "changes.query", "changes.query", {"recent_deployment": True}, "bad_deployment"),
        (
            "invoke_expert",
            "db",
            "db.connections",
            {"active_connections": 99, "max_connections": 100},
            "db_connection_exhausted",
        ),
    ],
)
async def test_failed_gate_plans_then_ranks_new_evidence(action, target, tool, observations, cause):
    payloads = []

    async def decide(**kwargs):
        payloads.append(kwargs["payload"])
        return {"action": action, "target": target, "reason": "inspect missing source"}

    registry = build_default_registry()
    investigator = AdaptiveInvestigator(
        registry,
        planner=LLMAdaptivePlanner(registry, llm_enabled=True, json_call=decide),
        settings=RuntimeSettings(evidence_gate_min_sources=1, evidence_gate_confidence=0.75),
    )

    async def execute(name, *_):
        return result(name, observations if name == tool else {})

    outcome = await investigator.run(alert(), execute)
    assert len(payloads) == 1
    assert set(payloads[0]) == {
        "alert",
        "evidence",
        "provisional_top_k",
        "executed_tools",
        "invoked_experts",
        "expert_capabilities",
        "action_history",
        "remaining_budget",
        "allowed_actions",
    }
    assert set(payloads[0]["expert_capabilities"]) <= set(payloads[0]["allowed_actions"]["invoke_expert"])
    assert [gate.sufficient for gate in outcome.trace.gate_decisions] == [False, True]
    assert outcome.provisional_candidates[0].root_cause_type.value == cause
    assert investigator.analysis_engine.analysis_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits",
    [
        {"investigation_max_rounds": 1},
        {"investigation_max_tool_calls": 1},
        {"investigation_max_expert_calls": 0},
        {"investigation_max_expert_calls": 1},
    ],
)
async def test_budget_terminates_without_exceeding_limits(limits):
    settings = RuntimeSettings(**limits)
    calls = []

    async def execute(name, *_):
        calls.append(name)
        return result(name, {})

    outcome = await AdaptiveInvestigator(build_default_registry(), settings=settings).run(alert(), execute)
    assert len(calls) <= settings.investigation_max_tool_calls
    assert outcome.trace.rounds <= settings.investigation_max_rounds
    assert len(outcome.trace.invoked_experts) <= settings.investigation_max_expert_calls
    assert outcome.trace.stop_reason
    if settings.investigation_max_expert_calls == 0:
        assert len(calls) > 2  # Disabling Experts still allows supplementary general tools.


@pytest.mark.asyncio
async def test_resume_partial_expert_uses_only_checkpointed_tool_results_and_evidence():
    registry = build_default_registry()
    item = alert().model_copy(update={"description": "database read slow query connections"})
    investigator = AdaptiveInvestigator(registry)
    calls = []
    checkpoint = None

    async def execute(name, *_):
        calls.append(name)
        observations = {"replication_lag_seconds": 20} if name == "db.replication" else {}
        if name == "traces.query":
            observations = {"traces": [{"trace_id": "db", "spans": [{"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 10}, {"span_id": "db", "parent_span_id": "root", "service": "mysql", "status": "ERROR", "duration_ms": 1500}]}]}
        return result(name, observations)

    async def crash(event, state):
        nonlocal checkpoint
        if event == "investigation.tool.completed" and state["executed_tools"][-1] == "db.replication":
            checkpoint = state
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        await investigator.run(item, execute, on_state=crash)
    assert checkpoint is not None
    assert not {"dimension_results", "expert_results", "matched_rules", "algorithm_signals"} & checkpoint.keys()
    restored = await AdaptiveInvestigator(registry).run(item, execute, restored_state=checkpoint)
    assert calls.count("db.replication") == 1
    assert "db.slowlog" in calls and "db.connections" in calls
    assert restored.provisional_candidates[0].root_cause_type == RootCauseType.DB_REPLICATION_LAG
    assert all(action.status != "planned" for action in restored.trace.action_history)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["unknown", "db"])
async def test_llm_cannot_invoke_unknown_or_budget_exhausted_expert(target):
    payload = None

    async def decide(**kwargs):
        nonlocal payload
        payload = kwargs["payload"]
        return {"action": "invoke_expert", "target": target, "reason": "invalid"}

    registry = build_default_registry()
    planner = LLMAdaptivePlanner(registry, llm_enabled=True, json_call=decide)
    action = await planner.decide(
        alert=alert(),
        round_number=2,
        evidence=[],
        candidates=[],
        executed_tools=["metrics.query", "logs.query"],
        invoked_experts=[],
        action_history=[],
        action_identities=set(),
        remaining_round_budget=2,
        remaining_tool_budget=5,
        remaining_expert_budget=0,
    )
    assert payload["allowed_actions"]["invoke_expert"] == []
    assert action.action_type.value == "inspect_tool"
    assert planner.last_fallback_reason
    assert not planner.last_used_llm

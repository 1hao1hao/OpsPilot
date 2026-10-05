import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from opspilot.config import RuntimeSettings
from opspilot.otel_benchmark.experiment import assert_frozen, mode_settings
from opspilot.otel_benchmark.experiment_report import aggregate, completed_records, knowledge_decision
from opspilot.otel_benchmark.isolation import LeakageGuard
from opspilot.otel_benchmark.modes import MeasuredClient, MeasuredPlanner, fixed_observe, llm_metrics, mode_order
from opspilot.otel_benchmark.schema import load_dataset, make_alert
from opspilot.tools import build_default_registry

ROOT = Path(__file__).parents[2]


def test_release_has_actual_three_repeats_and_rotates_all_modes():
    config = yaml.safe_load((ROOT / "benchmarks/datasets/otel_demo/v1/experiment.yaml").read_text())
    assert config["repeats"] == 3
    orders = [mode_order(i) for i in range(1, 4)]
    for position in range(3):
        assert {order[position] for order in orders} == set(config["modes"])


def test_no_l2_changes_only_expert_budget_and_fixed_disables_llm():
    config = {"rca": {}, "llm": {"model": "configured-model", "base_url": "https://example.invalid", "timeout_seconds": 30}}
    base = RuntimeSettings(_env_file=None)
    full = mode_settings(base, config, "full_adaptive").model_dump()
    no_l2 = mode_settings(base, config, "adaptive_no_l2").model_dump()
    assert full["llm_enabled"] and no_l2["llm_enabled"]
    assert no_l2["investigation_max_expert_calls"] == 0
    full["investigation_max_expert_calls"] = 0
    assert full == no_l2
    assert not mode_settings(base, config, "fixed_full").llm_enabled


async def test_fixed_observation_executes_all_tools_and_no_invented_experts():
    registry = build_default_registry()
    alert = make_alert(load_dataset(ROOT / "benchmarks/datasets/otel_demo/v1/scenarios.yaml").scenarios[0].alert_template, datetime.now(UTC))
    for name in registry.names():
        async def empty(payload):
            return {"observations": {}}
        registry.get(name).handler = empty
    outcome = await fixed_observe(registry, alert, RuntimeSettings(_env_file=None))
    assert len(outcome.tool_results) == 13
    assert outcome.trace.tool_budget_used == 13
    assert outcome.trace.expert_budget_used == 0
    assert not outcome.trace.invoked_experts
    assert outcome.provisional_candidates[0].root_cause_type.value == "no_fault"


def planner_kwargs(registry):
    alert = make_alert(load_dataset(ROOT / "benchmarks/datasets/otel_demo/v1/scenarios.yaml").scenarios[0].alert_template, datetime.now(UTC))
    from opspilot.agents import RootCauseAgent
    candidates, _ = RootCauseAgent().diagnose(alert, [])
    return {"alert": alert, "round_number": 2, "evidence": [], "candidates": candidates, "executed_tools": [], "invoked_experts": [],
            "action_history": [], "action_identities": set(), "remaining_round_budget": 3, "remaining_tool_budget": 8, "remaining_expert_budget": 0}


async def test_api_success_invalid_planner_decision_is_fallback_not_real_planner_usage(tmp_path):
    def response(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"action": "invoke_expert", "target": "db", "reason": "inspect"})}}],
                                        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, "model": "test-model"})
    guard = LeakageGuard(["paymentFailure"])
    client = MeasuredClient(tmp_path, guard, api_key="unit-test", model="test-model", max_attempts=1, transport=httpx.MockTransport(response))
    registry = build_default_registry()
    planner = MeasuredPlanner(registry, client=client, directory=tmp_path, guard=guard)
    action = await planner.decide(**planner_kwargs(registry))
    assert action is not None and action.action_type.value == "inspect_tool"
    usage = llm_metrics(client, planner)
    assert usage["api_calls"] == usage["api_successes"] == 1
    assert usage["planner_successes"] == 0 and usage["planner_fallbacks"] == 1
    assert not usage["llm_used"] and usage["fallback_occurred"]
    assert usage["tokens"]["total_tokens"] == 30
    assert "unit-test" not in (tmp_path / "llm/api-01.json").read_text()


async def test_api_failure_recorded_and_reliable_fallback_preserved(tmp_path):
    client = MeasuredClient(tmp_path, LeakageGuard([]), api_key="unit-test", model="test-model", max_attempts=1,
                            transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    registry = build_default_registry()
    planner = MeasuredPlanner(registry, client=client, directory=tmp_path, guard=LeakageGuard([]))
    await planner.decide(**planner_kwargs(registry))
    usage = llm_metrics(client, planner)
    assert usage["api_failures"] == 1 and usage["fallback_occurred"]
    assert not usage["llm_used"] and usage["usage_available_calls"] == 0


async def test_llm_requests_cannot_include_truth(tmp_path):
    client = MeasuredClient(tmp_path, LeakageGuard(["paymentFailure"]), api_key="unit-test", model="test-model")
    with pytest.raises(ValueError, match="boundary"):
        await client.complete_json(system_prompt="plan", payload={"flag": "paymentFailure"})
    assert not client.calls


async def test_json_client_satisfies_real_api_json_prompt_requirement(tmp_path):
    def respond(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "json" in body["messages"][0]["content"].lower()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action":"inspect_tool","target":"traces.query","reason":"inspect"}'}}]})
    client = MeasuredClient(tmp_path, LeakageGuard([]), api_key="test", model="test", transport=httpx.MockTransport(respond))
    result = await client.complete_json(system_prompt="Choose a permitted action", payload={"tools": ["traces.query"]})
    assert result["target"] == "traces.query"


def test_frozen_source_changes_stop_execution(tmp_path, monkeypatch):
    import hashlib

    from opspilot.otel_benchmark import experiment
    source = tmp_path / "code.py"
    source.write_text("original")
    (tmp_path / "source_manifest.json").write_text(json.dumps({"code.py": hashlib.sha256(source.read_bytes()).hexdigest()}))
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    assert_frozen(tmp_path)
    source.write_text("changed")
    with pytest.raises(RuntimeError, match="Frozen"):
        assert_frozen(tmp_path)


def test_metrics_separate_normal_faults_and_compute_actual_latency_percentiles():
    empty_llm = llm_metrics(None, None)
    rows = []
    for expected, correct, latency, tools in [("rpc_error_rate", True, 10, 4), ("rpc_error_rate", False, 20, 6), ("no_fault", False, 100, 8)]:
        rows.append({"scenario_id": expected, "status": "completed", "fault_observed": expected != "no_fault",
                     "llm": empty_llm, "score": {"top1": correct, "top3": True, "normal_correct": correct if expected == "no_fault" else None,
                                                "gate_pass": False, "budget_exhausted": True, "degraded": False,
                                                "tool_calls": tools, "expert_calls": 0, "investigation_rounds": 4, "latency_ms": latency}})
    result = aggregate(rows)
    assert result["top1"]["numerator"] == 1 and result["top1"]["denominator"] == 2
    assert result["no_fault_accuracy"]["rate"] == 0
    assert result["avg_tool_calls"] == 6
    assert result["p50_latency_ms"] == 20
    assert result["p95_latency_ms"] == pytest.approx(92)
    assert result["estimated_model_cost"] is None


def test_missing_verified_history_cannot_justify_rag():
    assert knowledge_decision({"wrong_top1_cases": 9})["decision"] == "INCONCLUSIVE"


def test_incomplete_recovery_or_retries_do_not_inflate_formal_repeat_counts():
    base = {"scenario_id": "normal", "repetition": 1, "mode": "full_adaptive", "status": "completed",
            "recovery_health": True, "recovery_queries_success": True, "symptom_recovery_confirmed": None}
    rows = [{**base, "path": "first"}, {**base, "path": "failed", "symptom_recovery_confirmed": False},
            {**base, "path": "latest"}]
    assert [r["path"] for r in completed_records(rows)] == ["latest"]

from datetime import UTC, datetime

import pytest

from opspilot.config import RuntimeSettings
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.decision import decide_diagnosis, prepare_decision
from opspilot.investigation.engine import AdaptiveInvestigator
from opspilot.investigation.planner import (
    ActionValidator,
    DeterministicPlannerFallback,
    EvidenceGate,
    LLMAdaptivePlanner,
)
from opspilot.investigation.report import build_report
from opspilot.models import AlertEvent, DiagnosisVerdict, RootCauseCandidate, RootCauseType, ToolResult
from opspilot.tools import build_default_registry
from opspilot.tools.executor import build_tool_call_id


def alert(kind="resource", description="CPU utilization"):
    return AlertEvent(alert_id="dynamic", service_name="checkout", alert_type=kind, severity="P1",
                      timestamp=datetime(2026, 10, 17, tzinfo=UTC), description=description)


def result(name, observations, *, target=None, identity=None):
    return ToolResult(tool_call_id=identity or f"{name}:{target or 'checkout'}", tool_name=name,
                      target_service=target, status="success", latency_ms=1,
                      data={"observations": observations})


def span_trace(item, *, status="TIMEOUT", service="checkout", child="payment", kind="client"):
    stamp = item.timestamp.timestamp()
    spans = [{"span_id": "root", "service": "checkout", "span_kind": "server", "status": "OK",
              "start_time": stamp, "duration_ms": 10},
             {"span_id": "call", "parent_span_id": "root", "service": service,
              "span_kind": kind, "status": status, "start_time": stamp, "duration_ms": 1500}]
    if child:
        spans.append({"span_id": "peer", "parent_span_id": "call", "service": child,
                      "span_kind": "server", "status": status, "start_time": stamp, "duration_ms": 1500})
    return result("traces.query", {"traces": [{"trace_id": "t", "spans": spans}]})


def cpu(value, *, start=None, end=None):
    data = {"current": value, "reference_series": [value] * 4, "recent_series": [value] * 4}
    if start is not None:
        data.update(window_start=start, window_end=end)
    return data


def candidates(evidence):
    ids = [item.evidence_id for item in evidence if RootCauseType.RESOURCE_SATURATION in item.supports]
    return [RootCauseCandidate(rank=1, root_cause_type=RootCauseType.RESOURCE_SATURATION,
                               summary="resource", confidence=0.95, evidence_ids=ids)]


def fallback(item, evidence, top, requirements, executed):
    registry = build_default_registry()
    return DeterministicPlannerFallback(registry, ActionValidator(registry)).decide(
        alert=item, round_number=2, evidence=evidence, candidates=top, executed_tools=executed,
        invoked_experts=[], action_history=[], action_identities=set(), remaining_round_budget=2,
        remaining_tool_budget=5, remaining_expert_budget=2,
        evidence_requirements=[r for r in requirements if r.active and not r.satisfied],
    )


def test_same_alert_and_top_k_different_observations_change_requirements_and_action():
    item = alert()
    base = [result("metrics.query", {"cpu_usage": 0.99, "qps": 10}), result("traces.query", {})]
    observed = base[:1] + [span_trace(item)]
    evidence = DeterministicEvidenceEngine().analyze(item, base).evidence
    top = candidates(evidence)
    plain = prepare_decision(item, base, evidence, top)
    changed = prepare_decision(item, observed, evidence, top)
    assert not any(r.signal == "rpc_timeout_rate" for r in plain.requirements)
    requirement = next(r for r in changed.requirements if r.signal == "rpc_timeout_rate")
    assert requirement.dependency == "payment" and requirement.service == "checkout"
    assert requirement.blocking and not requirement.satisfied and requirement.trigger_observation_ids
    assert requirement.suggested_tools == ["rpc.metrics"]
    plain_action = fallback(item, evidence, top, plain.requirements, ["metrics.query", "traces.query"])
    changed_action = fallback(item, evidence, top, changed.requirements, ["metrics.query", "traces.query"])
    assert plain_action.action_type.value == "inspect_tool"
    assert (changed_action.action_type.value, changed_action.target) == ("invoke_expert", "rpc")


def test_related_failures_and_message_chain_trigger_but_normal_and_unrelated_do_not():
    item = alert()
    top = candidates([])
    unrelated = span_trace(item, service="elsewhere", child=None)
    unrelated.data["observations"]["traces"][0]["spans"][1]["parent_span_id"] = "unrelated-root"
    for trace in (span_trace(item, status="OK"), unrelated):
        decision = prepare_decision(item, [trace], [], top)
        assert not any(r.purpose == "observed dependency" for r in decision.requirements)
    relevant = prepare_decision(item, [span_trace(item)], [], top)
    assert any(r.purpose == "observed dependency" for r in relevant.requirements)

    async_item = alert("custom", "Order completion is delayed")
    producer = span_trace(async_item, status="OK", child=None, kind="producer")
    async_requirements = prepare_decision(async_item, [producer], [], candidates([])).requirements
    lag = next(r for r in async_requirements if r.signal == "kafka_consumer_lag")
    assert lag.blocking and lag.suggested_tools == ["kafka.lag"]
    assert not any(r.signal == "kafka_consumer_lag" for r in prepare_decision(
        alert("custom", "Service health"), [producer], [], candidates([])).requirements)


def test_gate_uses_current_blocking_requirement_and_version_then_allows_measurement():
    item = alert()
    readings = [result("metrics.query", {"cpu_usage": 0.99, "qps": 10}), span_trace(item)]
    evidence = DeterministicEvidenceEngine().analyze(item, readings).evidence
    top = candidates(evidence)
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=1)
    assert gate.evaluate(top, evidence).sufficient  # Former rank-only gate passes.
    before = prepare_decision(item, readings, evidence, top)
    blocked = gate.evaluate(top, evidence, requirements=before.requirements,
                            requirements_version=before.requirements_version)
    assert not blocked.sufficient and blocked.unmet_requirement_ids
    assert decide_diagnosis(item, readings, evidence, top, blocked, prepared=before).verdict == DiagnosisVerdict.INCONCLUSIVE

    readings.append(result("rpc.metrics", {"call_volume": 30, "timeout_rate": 0.2, "error_rate": 0}))
    after = prepare_decision(item, readings, evidence, top, previous_requirements=before.requirements,
                             previous_version=before.requirements_version)
    assert after.requirements_version > before.requirements_version
    assert next(r for r in after.requirements if r.signal == "rpc_timeout_rate").satisfied
    current = gate.evaluate(top, evidence, requirements=after.requirements,
                            requirements_version=after.requirements_version)
    assert current.sufficient
    assert decide_diagnosis(item, readings, evidence, top, blocked, prepared=after).verdict == DiagnosisVerdict.INCONCLUSIVE
    assert decide_diagnosis(item, readings, evidence, top, current, prepared=after).verdict == DiagnosisVerdict.CONFIRMED


def test_healthy_reference_still_proves_absence_without_dynamic_trigger():
    item = alert()
    readings = [result("metrics.query", {"cpu_usage": cpu(0.2), "qps": 10})]
    evidence = DeterministicEvidenceEngine().analyze(item, readings).evidence
    top = candidates(evidence) if evidence else DeterministicEvidenceEngine().analyze(item, readings).candidates
    prepared = prepare_decision(item, readings, evidence, top)
    gate = EvidenceGate(confidence=0.8, margin=0.15, min_sources=2).evaluate(
        top, evidence, requirements=prepared.requirements, requirements_version=prepared.requirements_version)
    assert decide_diagnosis(item, readings, evidence, top, gate, prepared=prepared).verdict == DiagnosisVerdict.NO_FAULT


def test_only_same_definition_and_overlapping_window_conflict():
    item = alert()
    stamp = item.timestamp.timestamp()
    healthy = result("metrics.query", {"cpu_usage": cpu(0.2, start=stamp - 50, end=stamp - 30), "qps": 10}, identity="old")
    fault = result("metrics.query", {"cpu_usage": {"current": 0.99, "window_start": stamp - 5,
                                                      "window_end": stamp + 5}}, identity="new")
    evidence = DeterministicEvidenceEngine().analyze(item, [healthy, fault]).evidence
    top = candidates(evidence)
    changed = prepare_decision(item, [healthy, fault], evidence, top)
    assert not changed.measurement_conflicts
    same_window = healthy.model_copy(deep=True)
    same_window.data["observations"]["cpu_usage"].update(window_start=stamp - 5, window_end=stamp + 5)
    conflict = prepare_decision(item, [same_window, fault], evidence, top)
    assert conflict.measurement_conflicts
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=1).evaluate(
        top, evidence, requirements=conflict.requirements, requirements_version=conflict.requirements_version)
    assert decide_diagnosis(item, [same_window, fault], evidence, top, gate, prepared=conflict).verdict == DiagnosisVerdict.INCONCLUSIVE


@pytest.mark.asyncio
async def test_budget_resume_and_report_recheck_requirement_version():
    item = alert("custom", "Order completion delayed")
    item.labels = {"dependency": "checkout"}  # Select traces among the seed tools.
    investigator = AdaptiveInvestigator(build_default_registry(), settings=RuntimeSettings(
        llm_enabled=False, investigation_max_rounds=1))
    snapshots = []
    calls = []

    async def execute(name, *args):
        calls.append((name, args[-1]))
        if name == "metrics.query":
            return result(name, {"cpu_usage": 0.99, "qps": 10})
        if name == "traces.query":
            return span_trace(item, status="OK", child=None, kind="producer")
        return result(name, {})

    async def save(_, snapshot):
        snapshots.append(snapshot)

    outcome = await investigator.run(item, execute, on_state=save)
    before = len(calls)
    assert outcome.trace.decision.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert outcome.trace.gate_decisions[-1].requirements_version == outcome.trace.decision.requirements_version
    restored = await AdaptiveInvestigator(build_default_registry(), settings=RuntimeSettings(
        llm_enabled=False, investigation_max_rounds=1)).run(item, execute, restored_state=snapshots[-1])
    assert len(calls) == before
    assert restored.trace.decision.requirements_version == outcome.trace.decision.requirements_version
    report = build_report(alert=item, outcome=restored, trace_id="versioned", started_at=item.timestamp,
                          rationale="provisional", llm_used=False)
    assert report.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert report.decision.requirements_version == restored.trace.gate_decisions[-1].requirements_version


def test_tool_identity_includes_target_service():
    item = alert()
    registry = build_default_registry()
    planner = DeterministicPlannerFallback(registry, ActionValidator(registry))
    requirement = next(r for r in prepare_decision(item, [span_trace(item)], [], candidates([])).requirements
                       if r.signal == "rpc_timeout_rate")
    targeted = requirement.model_copy(update={"service": "worker"})
    action = planner.decide(
        alert=item, round_number=2, evidence=[], candidates=candidates([]),
        executed_tools=["rpc.metrics"], executed_tool_targets=["rpc.metrics|checkout"],
        invoked_experts=[], action_history=[], action_identities=set(),
        remaining_round_budget=2, remaining_tool_budget=2, remaining_expert_budget=1,
        evidence_requirements=[targeted],
    )
    assert (action.target, action.arguments["service_name"]) == ("rpc", "worker")
    kwargs = {"trace_id": "run", "step_id": "round-2:rpc.metrics", "tool_name": "rpc.metrics", "version": "1.0"}
    first = build_tool_call_id(**kwargs, arguments={"alert": item.model_dump(mode="json")})
    second = build_tool_call_id(**kwargs, arguments={"alert": item.model_dump(mode="json"), "target_service": "worker"})
    assert first != second


@pytest.mark.asyncio
async def test_complete_loop_uses_new_observation_with_same_top_k():
    item = alert()
    item.labels = {"dependency": "trace"}  # Include the read-only trace seed.

    class FixedRanker:
        def diagnose(self, _alert, evidence):
            return candidates(evidence), "fixed deterministic ranking for the test"

    async def investigate(failed):
        calls = []

        async def execute(name, _round, _reason, target):
            calls.append((name, target))
            if name == "metrics.query":
                return result(name, {"cpu_usage": 0.99, "qps": 10})
            if name == "traces.query":
                trace = span_trace(item, status="TIMEOUT" if failed else "OK")
                if not failed:
                    for span in trace.data["observations"]["traces"][0]["spans"]:
                        span["duration_ms"] = 10
                return trace
            if name == "rpc.metrics":
                return result(name, {"call_volume": 30, "timeout_rate": 0.2,
                                     "error_rate": 0}, target=target)
            return result(name, {})

        investigator = AdaptiveInvestigator(
            build_default_registry(), settings=RuntimeSettings(llm_enabled=False,
                evidence_gate_min_sources=1, investigation_max_rounds=2),
            analysis_engine=DeterministicEvidenceEngine(ranker=FixedRanker()),
        )
        return await investigator.run(item, execute), calls

    normal, normal_calls = await investigate(False)
    observed, observed_calls = await investigate(True)
    assert normal.provisional_candidates[0].root_cause_type == observed.provisional_candidates[0].root_cause_type
    assert normal.trace.rounds == 1 and normal.trace.decision.verdict == DiagnosisVerdict.CONFIRMED
    assert not any(name == "rpc.metrics" for name, _ in normal_calls)
    assert observed.trace.rounds == 2 and observed.trace.decision.verdict == DiagnosisVerdict.CONFIRMED
    assert observed.trace.gate_decisions[0].sufficient is False
    assert observed.trace.gate_decisions[1].sufficient is True
    assert ("rpc.metrics", "checkout") in observed_calls
    assert observed.trace.decision.requirements_version == observed.trace.gate_decisions[-1].requirements_version


@pytest.mark.asyncio
async def test_same_tool_can_measure_two_scoped_targets_without_reusing_result():
    item = alert()
    item.labels = {"dependency": "trace"}
    registry = build_default_registry()
    requests = []

    class FixedRanker:
        def diagnose(self, _alert, evidence):
            return candidates(evidence), "stable top one"

    async def select(**_kwargs):
        target = "checkout" if not requests else "worker"
        requests.append(target)
        return {"action": "invoke_expert", "target": "rpc", "target_service": target,
                "reason": "measure observed dependency"}

    async def execute(name, _round, _reason, target):
        if name == "metrics.query":
            return result(name, {"cpu_usage": 0.99, "qps": 10})
        if name == "traces.query":
            trace = span_trace(item, service="worker", child="payment")
            return trace
        if name == "rpc.metrics":
            return result(name, {"call_volume": 30, "timeout_rate": 0.2 if target == "worker" else 0,
                                 "error_rate": 0}, target=target)
        return result(name, {})

    investigator = AdaptiveInvestigator(
        registry,
        settings=RuntimeSettings(llm_enabled=False, evidence_gate_min_sources=1,
                                 investigation_max_rounds=3, investigation_max_expert_calls=2),
        planner=LLMAdaptivePlanner(registry, llm_enabled=True, json_call=select),
        analysis_engine=DeterministicEvidenceEngine(ranker=FixedRanker()),
    )
    outcome = await investigator.run(item, execute)
    assert requests == ["checkout", "worker"]
    assert "rpc.metrics|checkout" in outcome.trace.executed_tool_targets
    assert "rpc.metrics|worker" in outcome.trace.executed_tool_targets
    assert len([r for r in outcome.tool_results if r.tool_name == "rpc.metrics"]) == 2
    assert outcome.trace.decision_history[1].verdict == DiagnosisVerdict.INCONCLUSIVE
    requirement = next(r for r in outcome.trace.decision.requirements if r.signal == "rpc_timeout_rate")
    assert requirement.service == "worker" and requirement.satisfied

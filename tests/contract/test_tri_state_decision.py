from datetime import UTC, datetime

import pytest

from opspilot.config import RuntimeSettings
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.decision import decide_diagnosis
from opspilot.investigation.engine import AdaptiveInvestigator
from opspilot.investigation.planner import EvidenceGate
from opspilot.investigation.report import build_report
from opspilot.models import AlertEvent, DiagnosisReport, DiagnosisVerdict, FaultPresence, ToolResult
from opspilot.tools import build_default_registry


def alert(kind="resource", description="CPU utilization"):
    return AlertEvent(alert_id="scope", service_name="checkout", alert_type=kind,
                      description=description, severity="P1", timestamp=datetime(2026, 10, 9, tzinfo=UTC))


def result(name="metrics.query", observations=None, **kwargs):
    return ToolResult(tool_call_id=name, tool_name=name, status="success", latency_ms=1,
                      data={"observations": observations or {}}, **kwargs)


def metric(value):
    return {"current": value, "reference_series": [value] * 4, "recent_series": [value] * 4}


def decide(item, results, *, exhausted=False, minimum_sources=1):
    analysis = DeterministicEvidenceEngine().analyze(item, results)
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=minimum_sources).evaluate(
        analysis.candidates, analysis.evidence, budget_exhausted=exhausted)
    return decide_diagnosis(item, results, analysis.evidence, analysis.candidates, gate)


def test_scoped_fault_confirmed_only_with_root_gate():
    readings = [result(observations={"cpu_usage": 0.99})]
    assert decide(alert(), readings).verdict == DiagnosisVerdict.CONFIRMED
    decision = decide(alert(), readings, exhausted=True, minimum_sources=2)
    assert decision.fault_presence == FaultPresence.PRESENT
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert not decision.root_cause_confirmed
    assert "not evidence sufficiency" in decision.reason


def test_healthy_reference_and_activity_prove_alert_scoped_absence():
    decision = decide(alert(), [result(observations={"cpu_usage": metric(0.2), "qps": 12})])
    assert decision.verdict == DiagnosisVerdict.NO_FAULT
    assert decision.fault_presence == FaultPresence.ABSENT
    assert all(r.satisfied for r in decision.requirements)


def test_alert_specific_threshold_prevents_false_healthy_conclusion():
    item = alert("error_rate", "request errors above service SLO")
    item.labels = {"metric": "error_rate", "threshold": "0.1", "unit": "percent"}
    decision = decide(item, [result(observations={"error_rate": metric(0.5), "qps": 12})])
    assert decision.fault_presence == FaultPresence.PRESENT
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE


def test_explicit_alert_scope_overrides_generic_resource_votes():
    item = alert()
    item.labels = {"metric": "cpu_usage", "threshold": "98", "unit": "percent"}
    decision = decide(item, [result(observations={"cpu_usage": metric(0.95), "qps": 12})])
    assert decision.verdict == DiagnosisVerdict.NO_FAULT


def test_fault_presence_does_not_depend_on_a_top1_vote():
    item = alert()
    readings = [result(observations={"cpu_usage": 0.99})]
    empty = DeterministicEvidenceEngine().analyze(item, [])
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=1).evaluate(empty.candidates, [])
    decision = decide_diagnosis(item, readings, [], empty.candidates, gate)
    assert decision.fault_presence == FaultPresence.PRESENT
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE


@pytest.mark.parametrize("readings,quality", [
    ([result()], "no_data"),
    ([ToolResult(tool_call_id="failed", tool_name="metrics.query", status="error",
                 error_code="TIMEOUT", latency_ms=1)], "failed"),
    ([result(observations={"cpu_usage": 0.2, "qps": 10})], "missing_baseline"),
    ([result(observations={"cpu_usage": metric(float("nan")), "qps": 10})], "no_data"),
    ([result(observations={"cpu_usage": metric(0.2), "qps": 0})], "no_data"),
])
def test_missing_failed_invalid_and_idle_observations_are_unknown(readings, quality):
    decision = decide(alert(), readings, exhausted=True)
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert decision.fault_presence == FaultPresence.UNKNOWN
    assert quality in {r.quality.value for r in decision.requirements}


def test_unrelated_service_and_alert_dimension_do_not_prove_fault():
    assert decide(alert(), [result(observations={"service": "elsewhere", "cpu_usage": 0.99})]).fault_presence == FaultPresence.UNKNOWN
    decision = decide(alert("error_rate", "request errors"), [result(observations={
        "error_rate": metric(0), "cpu_usage": 0.99, "qps": 10})])
    assert decision.verdict == DiagnosisVerdict.NO_FAULT


def test_outgoing_success_alone_cannot_prove_incoming_error_absence():
    decision = decide(alert("error_rate", "request errors"), [result("rpc.metrics", {
        "call_volume": 100, "error_rate": 0, "timeout_rate": 0})])
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE


def test_async_completion_cannot_be_proved_by_healthy_request_gauges():
    decision = decide(alert("custom", "Order processing completion is delayed"), [result(observations={
        "tp99": metric(20), "error_rate": metric(0), "cpu_usage": metric(0.2),
        "memory_usage": metric(0.3), "qps": 10})])
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert any(r.signal == "completion" and not r.satisfied for r in decision.requirements)


def test_dynamic_requirements_follow_measured_candidate_and_discharge_each_round():
    item = alert("custom", "Service health check")
    base = [result(observations={"tp99": metric(20), "error_rate": metric(0), "cpu_usage": metric(0.2),
                               "memory_usage": metric(0.3), "qps": 10})]
    assert not any(r.signal == "kafka_consumer_lag" for r in decide(item, base).requirements)
    lag = result("kafka.lag", {"consumer_lag": 20000})
    decision = decide(item, base + [lag])
    requirement = next(r for r in decision.requirements if r.signal == "kafka_consumer_lag")
    assert requirement.purpose == "root discrimination" and requirement.satisfied
    assert decision.fault_presence == FaultPresence.PRESENT


def test_logs_require_service_and_incident_time_context():
    item = alert("resource", "memory utilization")
    old = result("logs.query", {"logs": [{"timestamp": "2020-01-01T00:00:00Z",
                                          "service": "checkout", "message": "OOMKilled"}]})
    assert decide(item, [old]).fault_presence == FaultPresence.UNKNOWN
    current = result("logs.query", {"logs": [{"timestamp": item.timestamp.isoformat(),
                                              "service": "checkout", "message": "OOMKilled"}]})
    assert decide(item, [current]).fault_presence == FaultPresence.PRESENT


def test_conflicting_measurements_do_not_confirm():
    normal = result(observations={"cpu_usage": metric(0.2), "qps": 10})
    abnormal = result(observations={"cpu_usage": 0.99})
    abnormal.tool_call_id = "another"
    decision = decide(alert(), [normal, abnormal])
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert decision.contradictions


def test_candidate_contradiction_does_not_erase_independently_observed_fault():
    item = alert()
    readings = [result(observations={"cpu_usage": 0.99})]
    analysis = DeterministicEvidenceEngine().analyze(item, readings)
    top = analysis.candidates[0]
    negative = analysis.evidence[0].model_copy(update={"evidence_id": "negative",
                                                     "supports": [], "contradicts": [top.root_cause_type]})
    gate = EvidenceGate(confidence=0.7, margin=0.1, min_sources=1).evaluate(analysis.candidates, analysis.evidence)
    decision = decide_diagnosis(item, readings, analysis.evidence + [negative], analysis.candidates, gate)
    assert decision.fault_presence == FaultPresence.PRESENT
    assert decision.verdict == DiagnosisVerdict.INCONCLUSIVE


def test_stale_and_sibling_failed_spans_do_not_establish_alert_presence():
    item = alert("error_rate", "request errors")
    spans = [
        {"span_id": "target", "service": "checkout", "status": "OK", "start_time": item.timestamp.timestamp()},
        {"span_id": "sibling", "service": "payment", "status": "ERROR", "start_time": item.timestamp.timestamp()},
        {"span_id": "old", "service": "checkout", "status": "ERROR", "start_time": item.timestamp.timestamp() - 600},
    ]
    decision = decide(item, [result("traces.query", {"traces": [{"trace_id": "t", "spans": spans}]})])
    assert decision.fault_presence == FaultPresence.UNKNOWN


@pytest.mark.asyncio
async def test_checkpoint_resume_and_report_compatibility():
    item = alert()
    investigator = AdaptiveInvestigator(build_default_registry(), settings=RuntimeSettings(llm_enabled=False))
    snapshots = []
    calls = []

    async def execute(name, *_):
        calls.append(name)
        return result(name, {"cpu_usage": metric(0.2), "qps": 10} if name == "metrics.query" else {})

    async def save(_, state):
        snapshots.append(state)

    outcome = await investigator.run(item, execute, on_state=save)
    assert outcome.trace.decision.verdict == DiagnosisVerdict.NO_FAULT
    count = len(calls)
    resumed = await investigator.run(item, execute, restored_state=snapshots[-1])
    assert len(calls) == count
    assert resumed.trace.decision.verdict == DiagnosisVerdict.NO_FAULT
    assert resumed.trace.decision_history
    report = build_report(alert=item, outcome=resumed, trace_id="r", started_at=item.timestamp,
                          rationale="legacy ranking explanation", llm_used=False)
    assert report.status == "completed"
    assert report.verdict == DiagnosisVerdict.NO_FAULT
    assert report.confirmed_root_cause is None
    legacy = report.model_dump(mode="json")
    for key in ("verdict", "decision", "confirmed_root_cause", "primary_root_cause_confirmed"):
        legacy.pop(key)
    legacy["schema_version"] = "2.0"
    restored = DiagnosisReport.model_validate(legacy)
    assert restored.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert not restored.primary_root_cause_confirmed


@pytest.mark.asyncio
async def test_budget_exhaustion_is_engineering_completion_without_confirmation():
    item = alert()
    investigator = AdaptiveInvestigator(build_default_registry(), settings=RuntimeSettings(
        llm_enabled=False, investigation_max_rounds=1))

    async def execute(name, *_):
        return result(name)

    outcome = await investigator.run(item, execute)
    report = build_report(alert=item, outcome=outcome, trace_id="empty", started_at=item.timestamp,
                          rationale="Top1 is provisional", llm_used=False)
    assert report.status == "completed"
    assert report.verdict == DiagnosisVerdict.INCONCLUSIVE
    assert report.candidates
    assert report.confirmed_root_cause is None

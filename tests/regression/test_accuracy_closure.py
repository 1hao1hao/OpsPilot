"""General temporal and causal evidence rules, with no benchmark case IDs."""

from datetime import UTC, datetime

from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.models import AlertEvent, ToolResult


def analyze(*observations):
    event = AlertEvent(alert_id="independent", service_name="api", alert_type="custom", severity="P2",
                       timestamp=datetime(2026, 1, 1, tzinfo=UTC))
    results = [ToolResult(tool_call_id=f"call-{i}", tool_name=name, status="success",
                          data={"observations": value}, latency_ms=1)
               for i, (name, value) in enumerate(observations)]
    return DeterministicEvidenceEngine().analyze(event, results)


def series(before, after):
    return {"current": after[-1], "reference_series": before, "baseline_series": before,
            "recent_series": after, "temporal_context": True}


def test_subthreshold_queue_growth_and_stable_large_queue():
    rising = analyze(("kafka.lag", {"consumer_lag": series([0] * 12, [540, 580, 620, 660])}))
    assert rising.candidates[0].root_cause_type.value == "kafka_consumer_lag"
    stable = analyze(("kafka.lag", {"consumer_lag": series([5000] * 12, [5000] * 4)}))
    assert stable.candidates[0].root_cause_type.value == "no_fault"


def test_stable_low_hit_rate_and_material_drop_without_memory_claim():
    stable = analyze(("redis.hotkeys", {"hit_rate_percent": series([35] * 12, [34, 35, 36, 35])}))
    assert stable.candidates[0].root_cause_type.value == "no_fault"
    dropped = analyze(("redis.hotkeys", {"hit_rate_percent": series([95] * 12, [40] * 4)}))
    assert [c.root_cause_type.value for c in dropped.candidates] == ["redis_low_hit_rate"]


def test_no_history_does_not_manufacture_temporal_diagnosis():
    result = analyze(("kafka.lag", {"consumer_lag": series([], [8000])}),
                     ("redis.hotkeys", {"hit_rate_percent": series([], [20])}))
    assert result.candidates[0].root_cause_type.value == "no_fault"


def test_improving_latency_and_small_resource_noise_are_not_faults():
    result = analyze(("metrics.query", {"tp99": series([500] * 12, [100] * 4),
                                        "memory_usage": series([0.1] * 12, [0.11, 0.12, 0.13, 0.14])}))
    assert result.candidates[0].root_cause_type.value == "no_fault"


def test_material_resource_growth_below_saturation_threshold():
    result = analyze(("metrics.query", {"memory_usage": series([0.4] * 12, [0.48, 0.49, 0.50, 0.51])}))
    assert result.candidates[0].root_cause_type.value == "resource_saturation"


def spans(count, *, database=False, timeout=False):
    return {"traces": [{"trace_id": str(i), "spans": [
        {"span_id": "root", "service": "api", "status": "OK", "duration_ms": 1},
        {"span_id": "child", "parent_span_id": "root", "service": "dependency",
         "operation": "query" if database else "request", "duration_ms": 2500,
         "status": "TIMEOUT" if timeout else "OK", **({"db_system": "postgresql"} if database else {})},
    ]} for i in range(count)]}


def test_long_successful_span_is_diagnostic_context_without_timeout_claim():
    result = analyze(("traces.query", spans(40)))
    assert result.candidates[0].root_cause_type.value == "no_fault"
    assert result.evidence and not any(e.supports for e in result.evidence)


def test_duplicate_timeouts_cannot_outvote_a_direct_database_measurement():
    one = analyze(("traces.query", spans(1, timeout=True)), ("db.slowlog", {"slow_query_count": 20}))
    many = analyze(("traces.query", spans(100, timeout=True)), ("db.slowlog", {"slow_query_count": 20}))
    assert many.candidates[0].root_cause_type.value == "db_slow_query"
    assert [(c.root_cause_type, c.confidence) for c in one.candidates] == [
        (c.root_cause_type, c.confidence) for c in many.candidates]


def test_typed_slow_database_span_is_specific_to_query_latency():
    result = analyze(("traces.query", spans(2, database=True)))
    assert [c.root_cause_type.value for c in result.candidates] == ["db_slow_query"]

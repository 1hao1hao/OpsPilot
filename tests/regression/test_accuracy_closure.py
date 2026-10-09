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


def timestamped_database(durations, *, offset=0):
    value = spans(len(durations), database=True)
    for trace, duration in zip(value["traces"], durations, strict=True):
        trace["spans"][1].update(start_time=datetime(2026, 1, 1, tzinfo=UTC).timestamp() + offset,
                                 duration_ms=duration)
    return value


def test_old_slow_database_calls_cannot_override_current_rpc_errors():
    result = analyze(("traces.query", timestamped_database([30000] * 10, offset=-180)),
                     ("rpc.metrics", {"error_rate": 0.2, "timeout_rate": 0}))
    assert result.candidates[0].root_cause_type.value == "rpc_error_rate"
    assert all(not e.supports for e in result.evidence if e.evidence_type == "trace.db_latency_hint")


def test_isolated_or_small_latency_tail_is_context_not_database_root_cause():
    for durations in ([1500] + [10] * 48, [2000] * 4 + [80] * 27):
        result = analyze(("traces.query", timestamped_database(durations)))
        assert result.candidates[0].root_cause_type.value == "no_fault"
        assert any(e.evidence_type == "trace.db_latency_hint" for e in result.evidence)


def test_repeated_incident_local_database_latency_retains_specific_support():
    result = analyze(("traces.query", timestamped_database([15000] * 14 + [10] * 120)),
                     ("rpc.metrics", {"error_rate": 0.3}))
    assert result.candidates[0].root_cause_type.value == "db_slow_query"


def test_observed_zero_timeouts_do_not_turn_latency_growth_into_timeouts():
    value = {"timeout_rate": 0, "error_rate": 0, "latency_ms": 500, "baseline_latency_ms": 50}
    result = analyze(("rpc.metrics", value))
    assert result.candidates[0].root_cause_type.value == "no_fault"
    result = analyze(("rpc.metrics", dict(value, timeout_rate=0.2)))
    assert result.candidates[0].root_cause_type.value == "rpc_timeout"


def test_domain_slow_query_count_requires_live_cohort_corroboration():
    value = {"slow_query_count": 30, "database_latency_cohorts": [
        {"slow_count": 30, "corroborated": False}]}
    result = analyze(("db.slowlog", value))
    assert result.candidates[0].root_cause_type.value == "no_fault"
    value["database_latency_cohorts"][0]["corroborated"] = True
    result = analyze(("db.slowlog", value))
    assert result.candidates[0].root_cause_type.value == "db_slow_query"


def test_duplicate_database_spans_do_not_manufacture_a_supported_cohort():
    value = timestamped_database([30000])
    value["traces"] *= 10
    result = analyze(("traces.query", value))
    assert result.candidates[0].root_cause_type.value == "no_fault"


def test_resource_baseline_excludes_a_previous_regime_after_a_large_drop():
    value = series([0.78, 0.47, 0.471, 0.5145, 0.515, 0.5104], [0.575, 0.567, 0.567, 0.579])
    result = analyze(("metrics.query", {"memory_usage": value}))
    assert result.candidates[0].root_cause_type.value == "resource_saturation"
    assert any("discarded_before_discontinuity" in e.fact for e in result.evidence)


def test_resource_reset_without_growth_or_with_insufficient_new_history_is_not_fault():
    for before in ([0.78, 0.47, 0.471, 0.472], [0.78, 0.47, 0.48]):
        result = analyze(("metrics.query", {"memory_usage": series(before, [0.48] * 4)}))
        assert result.candidates[0].root_cause_type.value == "no_fault"


def test_normal_sampled_rpc_outcomes_are_visible_without_causal_votes():
    result = analyze(("rpc.metrics", {"timeout_rate": 0, "error_rate": 0, "call_volume": 20}))
    assert result.candidates[0].root_cause_type.value == "no_fault"
    observed = next(e for e in result.evidence if e.evidence_type == "rpc.observed_outcomes")
    assert "'timeout_rate': 0" in observed.fact and not observed.supports
    missing = analyze(("rpc.metrics", {"call_volume": 20}))
    assert "timeout_rate" not in missing.evidence[0].fact

from opspilot.evaluation.dataset import load_dataset
from opspilot.evaluation.metrics import compute_metrics


def test_metrics_cover_top3_partial_evidence_failed_run_and_zero_tool_denominator():
    cases = load_dataset("benchmarks/datasets/rca/v1").cases("dev")[:2]
    predictions = [
        {
            "case_id": cases[0].case_id,
            "status": "completed",
            "candidate_types": ["rpc_timeout", cases[0].expected_root_cause_type.value],
            "evidence_types": cases[0].expected_evidence_types,
            "tool_executions": [],
            "latency_ms": 10,
        },
        {
            "case_id": cases[1].case_id,
            "status": "failed",
            "candidate_types": [],
            "evidence_types": [],
            "tool_executions": [],
            "latency_ms": 20,
        },
    ]
    metrics = compute_metrics(cases, predictions)
    assert metrics["root_cause_hit_at_1"]["value"] == 0
    assert metrics["root_cause_hit_at_3"]["value"] == 0.5
    assert metrics["evidence_recall_macro"]["value"] == 0.5
    assert metrics["e2e_success_rate"]["value"] == 0.5
    assert metrics["tool_success_rate"]["value"] is None
    assert metrics["p95_latency_ms"] == 20


def test_empty_input_has_explicit_null_rates():
    metrics = compute_metrics([], [])
    assert metrics["root_cause_hit_at_1"]["value"] is None
    assert metrics["e2e_success_rate"]["value"] is None
    assert metrics["p95_latency_ms"] is None


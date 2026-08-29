"""Metric formulas with explicit numerators and denominators."""

from __future__ import annotations

import math
from typing import Any

from opspilot.models import EvaluationCase, RootCauseType


def _ratio(numerator: float, denominator: int) -> dict[str, Any]:
    return {
        "value": round(float(numerator) / denominator, 6) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def compute_metrics(cases: list[EvaluationCase], predictions: list[dict]) -> dict[str, Any]:
    by_id = {prediction["case_id"]: prediction for prediction in predictions}
    fault_cases = [case for case in cases if case.is_fault]
    hit1 = 0
    hit3 = 0
    evidence_recalls: list[float] = []
    e2e = 0
    false_positives = 0
    normal_count = 0
    tool_success = 0
    tool_attempted = 0
    latencies: list[float] = []
    api_call_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    for case in cases:
        prediction = by_id.get(case.case_id, {"status": "missing", "candidate_types": []})
        candidates = prediction.get("candidate_types", [])
        if prediction.get("status") == "completed" and candidates:
            e2e += 1
        if case.is_fault:
            expected = case.expected_root_cause_type.value
            hit1 += bool(candidates and candidates[0] == expected)
            hit3 += expected in candidates[:3]
            expected_evidence = set(case.expected_evidence_types)
            actual_evidence = set(prediction.get("evidence_types", []))
            if expected_evidence:
                evidence_recalls.append(len(expected_evidence & actual_evidence) / len(expected_evidence))
        else:
            normal_count += 1
            false_positives += bool(candidates and candidates[0] != RootCauseType.NO_FAULT.value)
        for execution in prediction.get("tool_executions", []):
            tool_attempted += 1
            tool_success += execution.get("status") == "success"
        if isinstance(prediction.get("latency_ms"), (int, float)):
            latencies.append(float(prediction["latency_ms"]))
        usage = prediction.get("token_usage")
        if isinstance(usage, dict):
            api_call_count += 1
            prompt_tokens += int(usage.get("prompt_tokens", 0))
            completion_tokens += int(usage.get("completion_tokens", 0))
            total_tokens += int(usage.get("total_tokens", 0))

    return {
        "case_count": len(cases),
        "root_cause_hit_at_1": _ratio(hit1, len(fault_cases)),
        "root_cause_hit_at_3": _ratio(hit3, len(fault_cases)),
        "evidence_recall_macro": {
            "value": round(sum(evidence_recalls) / len(evidence_recalls), 6) if evidence_recalls else None,
            "scored_cases": len(evidence_recalls),
        },
        "tool_success_rate": _ratio(tool_success, tool_attempted),
        "e2e_success_rate": _ratio(e2e, len(cases)),
        "false_positive_rate": _ratio(false_positives, normal_count),
        "p95_latency_ms": _p95(latencies),
        "latency_scope": "in-process end-to-end; Stage 1 has no queue time",
        "model_usage": {
            "api_call_count": api_call_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "average_total_tokens_per_call": (
                round(total_tokens / api_call_count, 3) if api_call_count else None
            ),
        },
    }

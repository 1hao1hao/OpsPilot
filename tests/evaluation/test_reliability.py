from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from opspilot.evaluation.reliability import ReliabilityConfig, compute_reliability_metrics


def test_runtime_fault_config_contains_exact_minimum_matrix():
    payload = yaml.safe_load(Path("benchmarks/configs/runtime_faults.yaml").read_text(encoding="utf-8"))
    config = ReliabilityConfig.model_validate(payload)
    assert len(config.cases) == 5
    assert sum(case.trials for case in config.cases) == 15
    worker_crash = next(case for case in config.cases if case.fault_type.value == "WorkerCrash")
    assert worker_crash.injection_point == "tool:traces.query"
    with pytest.raises(ValidationError, match="fault matrix mismatch"):
        ReliabilityConfig.model_validate({**payload, "cases": payload["cases"][:-1]})


def test_reliability_metrics_keep_denominators_and_failed_trials():
    trials = [
        {
            "fault_type": "WorkerCrash",
            "recovery_success": True,
            "e2e_success": True,
            "duplicate_successful_tool_executions": 0,
            "successful_tool_executions": 9,
            "recovery_latency_ms": 12.0,
            "e2e_latency_ms": 30.0,
        },
        {
            "fault_type": "WorkerCrash",
            "recovery_success": False,
            "e2e_success": False,
            "duplicate_successful_tool_executions": 1,
            "successful_tool_executions": 4,
            "recovery_latency_ms": 25.0,
            "e2e_latency_ms": 40.0,
        },
    ]
    metrics = compute_reliability_metrics(trials)
    assert metrics["recovery_success_rate"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    assert metrics["duplicate_successful_tool_executions"] == {"value": 1, "denominator": 13}
    assert metrics["p95_recovery_latency_ms"] == 25.0
    assert metrics["by_fault_type"]["WorkerCrash"]["success_rate"]["denominator"] == 2

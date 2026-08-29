from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from opspilot.evaluation.ci_validation import validate_stage3_demos


def test_existing_stage3_evidence_satisfies_ci_contract():
    summary = validate_stage3_demos(
        reliability="artifacts/evaluations/20260812T061631Z-runtime-faults-v1",
        baseline="artifacts/evaluations/20260812T040942Z-deeprca_baseline-dev",
        hybrid="artifacts/evaluations/20260829T052113Z-opspilot_hybrid-dev",
    )
    assert summary == {"reliability_trials": 15, "baseline_cases": 24, "hybrid_cases": 24}


def test_stage3_evidence_rejects_failed_reliability_trial(tmp_path):
    source = "artifacts/evaluations/20260812T061631Z-runtime-faults-v1"
    reliability = tmp_path / "reliability"
    shutil.copytree(Path(source), reliability)
    (reliability / "failures.jsonl").write_text(json.dumps({"trial_id": "worker-crash-01"}) + "\n")
    with pytest.raises(ValueError, match="failed trials"):
        validate_stage3_demos(
            reliability=reliability,
            baseline="artifacts/evaluations/20260812T040942Z-deeprca_baseline-dev",
            hybrid="artifacts/evaluations/20260829T052113Z-opspilot_hybrid-dev",
        )


def test_stage3_evidence_rejects_incomplete_six_dimension_tool_plan(tmp_path):
    source = Path("artifacts/evaluations/20260829T052113Z-opspilot_hybrid-dev")
    hybrid = tmp_path / "hybrid"
    shutil.copytree(source, hybrid)
    metrics_path = hybrid / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["tool_success_rate"] = {"value": 1.0, "numerator": 239, "denominator": 239}
    metrics_path.write_text(json.dumps(metrics))

    with pytest.raises(ValueError, match="expected 240/240"):
        validate_stage3_demos(
            reliability="artifacts/evaluations/20260812T061631Z-runtime-faults-v1",
            baseline="artifacts/evaluations/20260812T040942Z-deeprca_baseline-dev",
            hybrid=hybrid,
        )

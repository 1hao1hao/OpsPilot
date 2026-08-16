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
        hybrid="artifacts/evaluations/20260812T045241Z-opspilot_hybrid-dev",
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
            hybrid="artifacts/evaluations/20260812T045241Z-opspilot_hybrid-dev",
        )

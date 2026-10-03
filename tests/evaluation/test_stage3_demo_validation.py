from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from opspilot.evaluation.ci_validation import validate_adaptive_demos


def test_adaptive_demo_evidence_satisfies_ci_contract():
    summary = validate_adaptive_demos(
        reliability="artifacts/evaluations/20260812T061631Z-runtime-faults-v1",
        fixed="artifacts/evaluations/20260830T102439Z-opspilot_fixed_planner-dev",
        adaptive="artifacts/evaluations/20260830T102440Z-opspilot_adaptive_planner-dev",
        without_l2="artifacts/evaluations/20260830T102441Z-opspilot_adaptive_without_dynamic_l2-dev",
        full="artifacts/evaluations/20260830T102441Z-opspilot_full_adaptive-dev",
        frozen="artifacts/evaluations/20260830T103157Z-opspilot_full_adaptive-test",
    )
    assert summary["reliability_trials"] == 15
    assert summary["case_count"] == 25
    assert summary["full_hit_at_1"] == 1.0
    assert summary["frozen_cases"] == 12
    assert summary["full_average_tools"] < summary["fixed_average_tools"]


def test_fixed_observation_ranking_regression_is_rejected(tmp_path):
    source = Path("artifacts/evaluations/20260830T102439Z-opspilot_fixed_planner-dev")
    fixed = tmp_path / "fixed"
    shutil.copytree(source, fixed)
    metrics_path = fixed / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["root_cause_hit_at_1"]["numerator"] -= 1
    metrics_path.write_text(json.dumps(metrics))
    with pytest.raises(ValueError, match="Fixed observation Hit@1"):
        validate_adaptive_demos(
            reliability="artifacts/evaluations/20260812T061631Z-runtime-faults-v1",
            fixed=fixed,
            adaptive="artifacts/evaluations/20260830T102440Z-opspilot_adaptive_planner-dev",
            without_l2="artifacts/evaluations/20260830T102441Z-opspilot_adaptive_without_dynamic_l2-dev",
            full="artifacts/evaluations/20260830T102441Z-opspilot_full_adaptive-dev",
        )


def test_failed_reliability_trial_is_still_rejected(tmp_path):
    from opspilot.evaluation.ci_validation import validate_reliability

    path = tmp_path / "reliability"
    shutil.copytree(Path("artifacts/evaluations/20260812T061631Z-runtime-faults-v1"), path)
    (path / "failures.jsonl").write_text(json.dumps({"trial_id": "worker-crash-01"}) + "\n")
    with pytest.raises(ValueError, match="failed trials"):
        validate_reliability(path)

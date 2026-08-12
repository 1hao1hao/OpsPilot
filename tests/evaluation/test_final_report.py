from __future__ import annotations

import json

import pytest

from opspilot.evaluation.final_report import build_final_evidence


def write_artifact(path, manifest: dict, metrics_name: str, metrics: dict) -> None:
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / metrics_name).write_text(json.dumps(metrics), encoding="utf-8")


def test_final_evidence_rejects_mismatched_dataset(tmp_path):
    baseline = tmp_path / "baseline"
    hybrid = tmp_path / "hybrid"
    reliability = tmp_path / "reliability"
    common = {"dataset_name": "rca", "dataset_version": "1", "split": "test", "case_count": 12}
    write_artifact(baseline, {**common, "evaluation_id": "base"}, "metrics.json", {})
    write_artifact(hybrid, {**common, "evaluation_id": "hybrid", "case_count": 11}, "metrics.json", {})
    write_artifact(reliability, {"evaluation_id": "runtime"}, "reliability.json", {})
    with pytest.raises(ValueError, match="same-dataset"):
        build_final_evidence(
            baseline_dir=baseline,
            hybrid_dir=hybrid,
            reliability_dir=reliability,
            output_path=tmp_path / "final.json",
        )

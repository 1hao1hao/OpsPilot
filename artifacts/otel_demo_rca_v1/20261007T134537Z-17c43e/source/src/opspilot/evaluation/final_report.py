"""Aggregate frozen business and runtime evidence without recomputing predictions."""

from __future__ import annotations

import json
from pathlib import Path


def build_final_evidence(
    *,
    baseline_dir: str | Path,
    hybrid_dir: str | Path,
    reliability_dir: str | Path,
    output_path: str | Path,
) -> Path:
    baseline_dir = Path(baseline_dir)
    hybrid_dir = Path(hybrid_dir)
    reliability_dir = Path(reliability_dir)
    output_path = Path(output_path)

    baseline_manifest = _read(baseline_dir / "manifest.json")
    hybrid_manifest = _read(hybrid_dir / "manifest.json")
    baseline_metrics = _read(baseline_dir / "metrics.json")
    hybrid_metrics = _read(hybrid_dir / "metrics.json")
    reliability_manifest = _read(reliability_dir / "manifest.json")
    reliability = _read(reliability_dir / "reliability.json")
    if (
        baseline_manifest["dataset_name"],
        baseline_manifest["dataset_version"],
        baseline_manifest["split"],
        baseline_manifest["case_count"],
    ) != (
        hybrid_manifest["dataset_name"],
        hybrid_manifest["dataset_version"],
        hybrid_manifest["split"],
        hybrid_manifest["case_count"],
    ):
        raise ValueError("baseline and hybrid artifacts are not same-dataset comparable")

    data = {
        "dataset": {
            "name": hybrid_manifest["dataset_name"],
            "version": hybrid_manifest["dataset_version"],
            "split": hybrid_manifest["split"],
            "case_count": hybrid_manifest["case_count"],
        },
        "baseline": {"evaluation_id": baseline_manifest["evaluation_id"], "metrics": baseline_metrics},
        "hybrid": {"evaluation_id": hybrid_manifest["evaluation_id"], "metrics": hybrid_metrics},
        "runtime": {"evaluation_id": reliability_manifest["evaluation_id"], "metrics": reliability},
        "evidence_paths": {
            "baseline": str(baseline_dir),
            "hybrid": str(hybrid_dir),
            "reliability": str(reliability_dir),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

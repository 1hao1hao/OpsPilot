"""Validate freshly generated Stage 3 demo evidence before CI accepts it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _expect_rate(metric: dict[str, Any], numerator: int, denominator: int, name: str) -> None:
    if metric.get("numerator") != numerator or metric.get("denominator") != denominator:
        raise ValueError(f"{name} expected {numerator}/{denominator}, got {metric}")


def validate_reliability(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    manifest = _json(path / "manifest.json")
    metrics = _json(path / "reliability.json")
    trials = _jsonl(path / "trials.jsonl")
    failures = _jsonl(path / "failures.jsonl")
    timelines = sorted((path / "trials").glob("*.jsonl"))
    if manifest.get("trial_count") != 15 or len(trials) != 15 or len(timelines) != 15:
        raise ValueError("reliability evidence must retain all 15 trials and timelines")
    if failures:
        raise ValueError(f"reliability demo has failed trials: {[item.get('trial_id') for item in failures]}")
    _expect_rate(metrics["recovery_success_rate"], 15, 15, "Recovery Success Rate")
    _expect_rate(metrics["e2e_success_rate"], 15, 15, "E2E Success Rate")
    duplicate = metrics["duplicate_successful_tool_executions"]
    if duplicate.get("value") != 0 or not duplicate.get("denominator"):
        raise ValueError(f"duplicate ToolExecution contract failed: {duplicate}")
    worker_crashes = [trial for trial in trials if trial["fault_type"] == "WorkerCrash"]
    if len(worker_crashes) != 3 or any(trial["worker_exit_codes"] != [97, 0, 0] for trial in worker_crashes):
        raise ValueError("WorkerCrash must use crash, recovery-scan and resumed worker processes")
    return metrics


def validate_evaluation(path: str | Path, expected_system: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path)
    manifest = _json(path / "manifest.json")
    metrics = _json(path / "metrics.json")
    predictions = _jsonl(path / "predictions.jsonl")
    _jsonl(path / "failures.jsonl")
    if manifest.get("system") != expected_system:
        raise ValueError(f"expected system {expected_system}, got {manifest.get('system')}")
    case_count = manifest.get("case_count")
    if manifest.get("split") != "dev" or not isinstance(case_count, int) or len(predictions) != case_count:
        raise ValueError(f"{expected_system} must retain every dev prediction")
    _expect_rate(metrics["e2e_success_rate"], case_count, case_count, f"{expected_system} E2E Success Rate")
    _expect_rate(metrics["false_positive_rate"], 0, 4, f"{expected_system} False Positive Rate")
    return manifest, metrics


def validate_stage3_demos(
    reliability: str | Path,
    baseline: str | Path,
    hybrid: str | Path,
) -> dict[str, Any]:
    reliability_metrics = validate_reliability(reliability)
    baseline_manifest, baseline_metrics = validate_evaluation(baseline, "deeprca_baseline")
    hybrid_manifest, hybrid_metrics = validate_evaluation(hybrid, "opspilot_hybrid")
    dataset_fields = ("dataset_name", "dataset_version", "split", "case_count")
    if any(baseline_manifest[field] != hybrid_manifest[field] for field in dataset_fields):
        raise ValueError("baseline and hybrid must use the same dataset/version/split/case count")
    _expect_rate(hybrid_metrics["root_cause_hit_at_1"], 20, 20, "hybrid Hit@1")
    _expect_rate(hybrid_metrics["root_cause_hit_at_3"], 20, 20, "hybrid Hit@3")
    # Runtime v2 executes the complete six-dimension plan: 10 registered
    # read-only tools for every one of the 24 dev cases.
    _expect_rate(hybrid_metrics["tool_success_rate"], 240, 240, "hybrid Tool Success Rate")
    if hybrid_metrics["evidence_recall_macro"].get("value") != 1.0:
        raise ValueError("hybrid Evidence Recall must remain 1.0 on the dev contract")
    return {
        "reliability_trials": reliability_metrics["trial_count"],
        "baseline_cases": baseline_metrics["case_count"],
        "hybrid_cases": hybrid_metrics["case_count"],
    }


def validate_adaptive_demos(
    reliability: str | Path,
    fixed: str | Path,
    adaptive: str | Path,
    without_l2: str | Path,
    full: str | Path,
    frozen: str | Path | None = None,
) -> dict[str, Any]:
    """Gate the Runtime v3 reliability matrix and four adaptive RCA ablations."""
    reliability_metrics = validate_reliability(reliability)
    paths = {
        "fixed": (fixed, "opspilot_fixed_planner"),
        "adaptive": (adaptive, "opspilot_adaptive_planner"),
        "without_l2": (without_l2, "opspilot_adaptive_without_dynamic_l2"),
        "full": (full, "opspilot_full_adaptive"),
    }
    validated = {name: validate_evaluation(path, system) for name, (path, system) in paths.items()}
    manifests = [item[0] for item in validated.values()]
    dataset_fields = ("dataset_name", "dataset_version", "split", "case_count")
    if any(tuple(manifest[field] for field in dataset_fields) != tuple(manifests[0][field] for field in dataset_fields) for manifest in manifests[1:]):
        raise ValueError("all adaptive ablations must use the same dataset/version/split/case count")
    fixed_metrics = validated["fixed"][1]
    full_metrics = validated["full"][1]
    fault_count = full_metrics["root_cause_hit_at_1"]["denominator"]
    _expect_rate(full_metrics["root_cause_hit_at_1"], fault_count, fault_count, "Full Adaptive Hit@1")
    _expect_rate(full_metrics["root_cause_hit_at_3"], fault_count, fault_count, "Full Adaptive Hit@3")
    if full_metrics["evidence_recall_macro"].get("value") != 1.0:
        raise ValueError("Full Adaptive Evidence Recall must remain 1.0")
    if full_metrics["average_tool_calls_per_case"] >= fixed_metrics["average_tool_calls_per_case"]:
        raise ValueError("Full Adaptive must use fewer average Tool calls than Fixed Planner")
    if full_metrics["average_expert_calls_per_case"] >= fixed_metrics["average_expert_calls_per_case"]:
        raise ValueError("Full Adaptive must use fewer average Expert calls than Fixed Planner")
    if full_metrics["planner_action_valid_rate"].get("value") != 1.0:
        raise ValueError("Planner Action Valid Rate must be 1.0")
    if full_metrics["duplicate_action_rate"].get("value") != 0.0:
        raise ValueError("Duplicate Action Rate must be 0.0")
    if frozen is not None:
        frozen_path = Path(frozen)
        frozen_manifest = _json(frozen_path / "manifest.json")
        frozen_metrics = _json(frozen_path / "metrics.json")
        frozen_predictions = _jsonl(frozen_path / "predictions.jsonl")
        frozen_failures = _jsonl(frozen_path / "failures.jsonl")
        if frozen_manifest.get("system") != "opspilot_full_adaptive" or frozen_manifest.get("split") != "test":
            raise ValueError("frozen run must be the Full Adaptive test split")
        if frozen_manifest.get("case_count") != 12 or len(frozen_predictions) != 12 or frozen_failures:
            raise ValueError("frozen run must retain 12 successful predictions and no failures")
        _expect_rate(frozen_metrics["root_cause_hit_at_1"], 10, 10, "Frozen Full Adaptive Hit@1")
        _expect_rate(frozen_metrics["false_positive_rate"], 0, 2, "Frozen Full Adaptive FPR")
    return {
        "reliability_trials": reliability_metrics["trial_count"],
        "case_count": full_metrics["case_count"],
        "full_hit_at_1": full_metrics["root_cause_hit_at_1"]["value"],
        "fixed_average_tools": fixed_metrics["average_tool_calls_per_case"],
        "full_average_tools": full_metrics["average_tool_calls_per_case"],
        "frozen_cases": 12 if frozen is not None else None,
    }

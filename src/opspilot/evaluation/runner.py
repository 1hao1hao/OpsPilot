"""Execute every case, retain failures, and write reproducible artifacts."""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from opspilot.evaluation.dataset import load_dataset
from opspilot.evaluation.metrics import compute_metrics
from opspilot.evaluation.systems import build_system


async def run_evaluation(config_path: str | Path, split_override: str | None = None) -> Path:
    config_path = Path(config_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    dataset = load_dataset(config["dataset_path"])
    split = split_override or config.get("split", "dev")
    cases = dataset.cases(split)
    system = build_system(config["system"])
    predictions: list[dict] = []
    failures: list[dict] = []

    for case in cases:
        try:
            prediction = await system.predict(case.alert, case.case_id)
        # A benchmark must retain unexpected per-case failures instead of
        # aborting the run and silently losing the denominator.
        except Exception as exc:  # noqa: BLE001
            prediction = {
                "case_id": case.case_id,
                "status": "failed",
                "candidate_types": [],
                "evidence_types": [],
                "tool_executions": [],
                "latency_ms": None,
                "degraded": True,
                "error": str(exc),
            }
        predictions.append(prediction)
        expected = case.expected_root_cause_type.value
        actual = prediction.get("candidate_types", [None])[0] if prediction.get("candidate_types") else None
        if prediction.get("status") != "completed" or actual != expected:
            failures.append(
                {
                    "case_id": case.case_id,
                    "failure_type": "runtime" if prediction.get("status") != "completed" else "root_cause",
                    "expected_root_cause_type": expected,
                    "actual_root_cause_type": actual,
                    "error": prediction.get("error"),
                }
            )

    metrics = compute_metrics(cases, predictions)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evaluation_id = f"{timestamp}-{system.name}-{split}"
    output_root = Path(config.get("artifact_root", "artifacts/evaluations")) / evaluation_id
    output_root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "evaluation_id": evaluation_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_name": dataset.manifest.name,
        "dataset_version": dataset.manifest.version,
        "dataset_schema_version": dataset.manifest.schema_version,
        "split": split,
        "case_count": len(cases),
        "system": system.name,
        "config_path": str(config_path),
        "config": config,
        "command": " ".join(sys.argv),
        "python": platform.python_version(),
        "git_sha": None,
        "git_note": "workspace is not a git repository",
    }
    _write_json(output_root / "manifest.json", manifest)
    _write_json(output_root / "metrics.json", metrics)
    _write_jsonl(output_root / "predictions.jsonl", predictions)
    _write_jsonl(output_root / "failures.jsonl", failures)
    (output_root / "report.md").write_text(_render_report(manifest, metrics, failures), encoding="utf-8")
    return output_root


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _render_report(manifest: dict, metrics: dict, failures: list[dict]) -> str:
    def value(name: str) -> str:
        metric = metrics[name]
        return "n/a" if metric["value"] is None else f"{metric['value']:.3f} ({metric['numerator']}/{metric['denominator']})"

    failed_ids = ", ".join(item["case_id"] for item in failures) or "none"
    return f"""# Evaluation Report

- Evaluation: `{manifest['evaluation_id']}`
- Dataset: `{manifest['dataset_name']}@{manifest['dataset_version']}`
- Split/cases: `{manifest['split']}` / {manifest['case_count']}
- System: `{manifest['system']}`

## Metrics

- Root Cause Hit@1: {value('root_cause_hit_at_1')}
- Root Cause Hit@3: {value('root_cause_hit_at_3')}
- Evidence Recall (macro): {metrics['evidence_recall_macro']['value']}
- Tool Success Rate: {value('tool_success_rate')}
- E2E Success Rate: {value('e2e_success_rate')}
- False Positive Rate: {value('false_positive_rate')}
- P95 latency: {metrics['p95_latency_ms']} ms

## Failures

{failed_ids}

Predictions are system-generated. Failed cases remain in `predictions.jsonl` and `failures.jsonl`.
"""

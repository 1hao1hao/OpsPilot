"""Controlled sequential-vs-parallel tool execution benchmark."""

from __future__ import annotations

import asyncio
import json
import math
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from opspilot.evaluation.dataset import load_dataset
from opspilot.evaluation.runner import _git_dirty, _git_sha
from opspilot.graph import OpsPilotWorkflow
from opspilot.models import ToolStatus
from opspilot.tools import build_default_registry
from opspilot.tools.registry import ObservationProvider


class FixedDelayObservationProvider(ObservationProvider):
    """Adds deterministic provider I/O latency while preserving dataset observations."""

    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    async def read(self, signal_key, alert):
        await asyncio.sleep(self.delay_seconds)
        return await super().read(signal_key, alert)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _mode_metrics(rows: list[dict], mode: str) -> dict:
    selected = [row for row in rows if row["execution_mode"] == mode]
    latencies = [row["latency_ms"] for row in selected]
    attempted = sum(row["tool_attempted"] for row in selected)
    succeeded = sum(row["tool_succeeded"] for row in selected)
    return {
        "run_count": len(selected),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "tool_success_rate": {
            "value": round(succeeded / attempted, 6) if attempted else None,
            "numerator": succeeded,
            "denominator": attempted,
        },
    }


async def run_concurrency_benchmark(config_path: str | Path) -> Path:
    config_path = Path(config_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    repeats = int(config.get("repeats", 3))
    delay_seconds = float(config.get("tool_delay_seconds", 0.02))
    if repeats < 1 or delay_seconds <= 0:
        raise ValueError("repeats must be >= 1 and tool_delay_seconds must be > 0")
    dataset = load_dataset(config["dataset_path"])
    split = config.get("split", "dev")
    cases = dataset.cases(split)
    workflows = {
        mode: OpsPilotWorkflow(
            build_default_registry(provider=FixedDelayObservationProvider(delay_seconds), timeout_seconds=2),
            execution_mode=mode,
        )
        for mode in ("sequential", "parallel")
    }
    rows: list[dict] = []
    for repeat in range(1, repeats + 1):
        modes = ("sequential", "parallel") if repeat % 2 else ("parallel", "sequential")
        for case in cases:
            for mode in modes:
                report = await workflows[mode].analyze(
                    case.alert,
                    trace_id=f"concurrency-{mode}-{case.case_id}-{repeat}",
                )
                rows.append(
                    {
                        "case_id": case.case_id,
                        "repeat": repeat,
                        "execution_mode": mode,
                        "latency_ms": round(report.latency_ms, 3),
                        "tool_attempted": len(report.tool_executions),
                        "tool_succeeded": sum(
                            execution.status == ToolStatus.SUCCESS for execution in report.tool_executions
                        ),
                    }
                )
    sequential = _mode_metrics(rows, "sequential")
    parallel = _mode_metrics(rows, "parallel")
    metrics = {
        "sequential": sequential,
        "parallel": parallel,
        "p95_speedup": round(sequential["p95_latency_ms"] / parallel["p95_latency_ms"], 3),
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evaluation_id = f"{timestamp}-{config.get('name', 'tool-concurrency')}"
    output = Path(config.get("artifact_root", "artifacts/evaluations")) / evaluation_id
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "evaluation_id": evaluation_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_name": dataset.manifest.name,
        "dataset_version": dataset.manifest.version,
        "split": split,
        "case_count": len(cases),
        "repeats": repeats,
        "tool_delay_seconds": delay_seconds,
        "latency_scope": "in-process workflow with fixed per-tool async provider delay",
        "config_path": str(config_path),
        "config": config,
        "command": " ".join(sys.argv),
        "python": platform.python_version(),
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "external_paid_api": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    (output / "runs.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output / "report.md").write_text(_render_report(manifest, metrics), encoding="utf-8")
    return output


def _render_report(manifest: dict, metrics: dict) -> str:
    sequential = metrics["sequential"]
    parallel = metrics["parallel"]
    return f"""# Tool Execution Concurrency Report

- Evaluation: `{manifest['evaluation_id']}`
- Dataset: `{manifest['dataset_name']}@{manifest['dataset_version']}` `{manifest['split']}`
- Cases / repeats: {manifest['case_count']} / {manifest['repeats']}
- Fixed async provider delay per tool: {manifest['tool_delay_seconds'] * 1000:.1f} ms

| Mode | Runs | P50 | P95 | Tool Success |
|---|---:|---:|---:|---:|
| Sequential | {sequential['run_count']} | {sequential['p50_latency_ms']} ms | {sequential['p95_latency_ms']} ms | {sequential['tool_success_rate']['numerator']}/{sequential['tool_success_rate']['denominator']} |
| Parallel | {parallel['run_count']} | {parallel['p50_latency_ms']} ms | {parallel['p95_latency_ms']} ms | {parallel['tool_success_rate']['numerator']}/{parallel['tool_success_rate']['denominator']} |

P95 speedup: **{metrics['p95_speedup']}x**.

Every measured run is retained in `runs.jsonl`. This controlled benchmark measures scheduling behavior,
not production network latency.
"""

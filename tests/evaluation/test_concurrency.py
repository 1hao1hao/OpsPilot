import json

import pytest
import yaml

from opspilot.evaluation.concurrency import run_concurrency_benchmark


@pytest.mark.asyncio
async def test_concurrency_benchmark_writes_same_workload_comparison(tmp_path):
    config = {
        "name": "test-concurrency",
        "dataset_path": "benchmarks/datasets/rca/v1",
        "split": "test",
        "artifact_root": str(tmp_path / "artifacts"),
        "repeats": 1,
        "tool_delay_seconds": 0.002,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output = await run_concurrency_benchmark(config_path)
    metrics = json.loads((output / "metrics.json").read_text())
    rows = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()]

    assert {path.name for path in output.iterdir()} == {"manifest.json", "metrics.json", "runs.jsonl", "report.md"}
    assert len(rows) == 24
    assert metrics["sequential"]["tool_success_rate"]["value"] == 1
    assert metrics["parallel"]["tool_success_rate"]["value"] == 1
    assert metrics["parallel"]["p95_latency_ms"] < metrics["sequential"]["p95_latency_ms"]

import json
from pathlib import Path

import pytest
import yaml

from opspilot.evaluation.runner import run_evaluation


def _load_hybrid_config():
    return yaml.safe_load(Path("benchmarks/configs/opspilot_hybrid.yaml").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_runner_writes_one_prediction_per_case_and_all_artifacts(tmp_path):
    config = _load_hybrid_config()
    config["artifact_root"] = str(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    output = await run_evaluation(config_path, "test")
    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "predictions.jsonl",
        "failures.jsonl",
        "report.md",
    }
    predictions = [json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()]
    assert len(predictions) == 12
    assert all("case_id" in prediction for prediction in predictions)

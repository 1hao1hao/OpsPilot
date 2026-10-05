"""Offline replay of actual recorded telemetry; never counted as live experiments."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opspilot.evidence import collect_evidence
from opspilot.models import ToolCall
from opspilot.otel_benchmark.isolation import LeakageGuard
from opspilot.otel_benchmark.schema import load_dataset, make_alert
from opspilot.tools import ToolExecutor, build_default_registry
from opspilot.tools.registry import ObservationOutput

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "benchmarks/datasets/otel_demo/v1/fixtures"
COMPLETED = [path for path in FIXTURES.rglob("metadata.json")
             if json.loads(path.read_text(encoding="utf-8")).get("lifecycle_complete")]


@pytest.mark.parametrize("metadata_path", COMPLETED or [None], ids=[str(p.parent.relative_to(FIXTURES)) for p in COMPLETED] or ["no-completed-recordings"])
async def test_recorded_observations_replay_through_existing_tools_and_evidence(metadata_path):
    if metadata_path is None:
        pytest.skip("No completed real experiment fixture yet")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    observations = json.loads((metadata_path.parent / "observations.json").read_text(encoding="utf-8"))
    dataset = load_dataset(ROOT / "benchmarks/datasets/otel_demo/v1/scenarios.yaml")
    scenario = next(s for s in dataset.scenarios if s.scenario_id == metadata["scenario"])
    guard = LeakageGuard(s.fault_control.flag for s in dataset.scenarios if s.fault_control)
    assert metadata["source"] == "recorded from OTel Demo"
    assert metadata["upstream_commit"] == dataset.upstream_version["commit"]
    assert datetime.fromisoformat(metadata["captured_at"]).tzinfo is not None
    guard.assert_clean(observations)
    registry = build_default_registry()
    for name in registry.names():
        recorded = observations.get(name, {})

        async def replay(payload, _observations=recorded):
            return ObservationOutput(observations=_observations)

        registry.get(name).handler = replay
    executor = ToolExecutor(registry)
    alert = make_alert(scenario.alert_template, datetime.now(UTC))
    results = []
    for name in registry.names():
        result = await executor.execute(ToolCall(tool_call_id=name, tool_name=name,
                                               arguments={"alert": alert.model_dump(mode="json")}))
        assert result.status == "success"
        guard.assert_clean(result.model_dump(mode="json"))
        results.append(result)
    for evidence in collect_evidence(alert, results):
        guard.assert_clean(evidence.model_dump(mode="json"))

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from opspilot.otel_benchmark.controller import OpenTelemetryDemoFaultController
from opspilot.otel_benchmark.isolation import LeakageGuard
from opspilot.otel_benchmark.schema import load_dataset, make_alert
from opspilot.otel_benchmark.scoring import score_report, summarize

DATASET = Path(__file__).parents[2] / "benchmarks/datasets/otel_demo/v1/scenarios.yaml"


def test_schema_has_seven_cases_and_release_repeats_and_neutral_alerts():
    data = load_dataset(DATASET)
    assert len(data.scenarios) == 7
    assert data.profiles["release"].repeat >= 3
    assert data.profiles["smoke"].normal_repeat >= 3
    guard = LeakageGuard(s.fault_control.flag for s in data.scenarios if s.fault_control)
    for scenario in data.scenarios:
        event = make_alert(scenario.alert_template, datetime.now(UTC)).model_dump(mode="json")
        guard.assert_clean(event)
        assert not event["signals"] and not event["labels"]
        assert "scenario_id" not in event and "ground_truth" not in event


def test_true_logs_cannot_disclose_fault_control_to_tools_or_evidence():
    guard = LeakageGuard(["paymentFailure", "kafkaQueueProblems"])
    observations = {"logs": [{"message": "FeatureFlag paymentFailure activated", "severity": "ERROR"},
                              {"message": "payment RPC returned error", "severity": "ERROR"}],
                    "expected_root_cause_type": "rpc_error_rate", "error_rate": 0.7}
    clean = guard.sanitize(observations)
    guard.assert_clean({"tool_result": clean, "evidence": clean})
    assert clean["logs"] == [{"message": "payment RPC returned error", "severity": "ERROR"}]
    assert clean["error_rate"] == 0.7
    assert guard.dropped == 2
    assert "paymentFailure" not in json.dumps(clean)


def test_control_service_spans_are_omitted_entirely_not_left_without_service():
    guard = LeakageGuard(["paymentFailure"])
    spans = [{"service": "flagd", "operation": "ResolveBoolean", "duration_ms": 1},
             {"service": "payment", "operation": "Charge", "duration_ms": 20}]
    assert guard.sanitize(spans) == [spans[1]]


def test_anonymous_injection_status_logs_are_control_plane_not_symptom_evidence():
    guard = LeakageGuard(["adHighCpu"])
    logs = [{"message": "High CPU-Load problempattern enabled", "severity": "INFO"},
            {"message": "CPU usage exceeded service threshold", "severity": "WARN"}]
    assert guard.sanitize(logs) == [logs[1]]


@pytest.mark.parametrize("payload", [
    {"description": "paymentFailure"}, {"expected_root_cause_type": "rpc_error_rate"},
    {"arguments": {"fault_control": "off"}}, {"signals": {"feature_flag_key": "paymentFailure"}},
    {"observations": {"paymentFailure": True}},
])
def test_ground_truth_boundary_rejects_alerts_arguments_and_results(payload):
    with pytest.raises(ValueError, match="boundary"):
        LeakageGuard(["paymentFailure"]).assert_clean(payload)


def test_control_names_in_json_keys_are_removed_without_changing_measurements():
    guard = LeakageGuard(["paymentFailure"])
    clean = guard.sanitize({"paymentFailure": True, "error_rate": 100.0})
    guard.assert_clean(clean)
    assert clean == {"error_rate": 100.0}


async def test_changes_query_cannot_access_flagd():
    from opspilot.config import RuntimeSettings
    from opspilot.models import ToolCall
    from opspilot.observations.provider import OpenTelemetryDemoProvider
    from opspilot.tools import ToolExecutor, build_default_registry

    def no_http(request):
        pytest.fail(f"changes.query accessed network: {request.url.path}")

    settings = RuntimeSettings(_env_file=None, observation_backend="otel_demo", prometheus_url="http://prom",
                               jaeger_url="http://jaeger", opensearch_url="http://logs")
    provider = OpenTelemetryDemoProvider(settings, transport=httpx.MockTransport(no_http))
    alert = make_alert(load_dataset(DATASET).scenarios[0].alert_template, datetime.now(UTC))
    result = await ToolExecutor(build_default_registry(provider=provider)).execute(
        ToolCall(tool_call_id="safe", tool_name="changes.query", arguments={"alert": alert.model_dump(mode="json")}))
    assert result.data == {"observations": {}}


async def test_capture_filters_real_pydantic_tool_boundary(tmp_path):
    from opspilot.config import RuntimeSettings
    from opspilot.models import ToolCall
    from opspilot.otel_benchmark.runner import Capture
    from opspilot.tools import ToolExecutor

    settings = RuntimeSettings(_env_file=None, observation_backend="otel_demo", prometheus_url="http://prom",
                               jaeger_url="http://jaeger", opensearch_url="http://logs")
    capture = Capture(tmp_path, settings, LeakageGuard(["paymentFailure"]))

    async def observations(*args):
        return {"logs": [{"message": "paymentFailure is on"}, {"message": "RPC unavailable"}]}

    capture.provider.read = observations
    alert = make_alert(load_dataset(DATASET).scenarios[0].alert_template, datetime.now(UTC))
    result = await ToolExecutor(capture.registry()).execute(ToolCall(
        tool_call_id="isolated", tool_name="logs.query", arguments={"alert": alert.model_dump(mode="json")}))
    assert result.data["observations"]["logs"] == [{"message": "RPC unavailable"}]
    capture.guard.assert_clean(result.model_dump(mode="json"))
    capture.flush()
    assert json.loads((tmp_path / "benchmark/isolation_audit.json").read_text())["control_records_removed"] == 1


async def test_controller_validates_and_waits_for_actual_reload_readback():
    state = {"flags": {"paymentFailure": {"variants": {"off": 0, "100%": 1}, "defaultVariant": "off"}}}
    saved = {}
    polls = 0

    def handler(request):
        nonlocal state, polls
        if request.url.path == "/api/read":
            return httpx.Response(200, json=state)
        if request.url.path == "/api/write":
            state = json.loads(request.content)["data"]
            return httpx.Response(200, json={"success": True})
        assert request.url.path.endswith("/ResolveInt")
        polls += 1
        return httpx.Response(200, json={"variant": "off" if polls == 1 else state["flags"]["paymentFailure"]["defaultVariant"]})

    controller = OpenTelemetryDemoFaultController(lambda name, value: saved.update({name: value}),
                                                   transport=httpx.MockTransport(handler))
    await controller.set_fault("paymentFailure", "100%")
    assert polls == 2
    assert any(name.endswith("_confirmed.json") for name in saved)
    with pytest.raises(ValueError):
        await controller.set_fault("missing", "on")
    await controller.reset_all_faults()
    assert state["flags"]["paymentFailure"]["defaultVariant"] == "off"


def test_scoring_uses_actual_candidates_and_separate_denominators():
    report = {"candidates": [{"root_cause_type": "rpc_timeout"}, {"root_cause_type": "rpc_error_rate"}],
              "investigation": {"gate_decisions": [{"sufficient": False, "budget_exhausted": True}],
                                "tool_budget_used": 3, "expert_budget_used": 1, "rounds": 2},
              "degraded": False, "latency_ms": 10}
    wrong_top1 = score_report(report, "rpc_error_rate")
    false_positive = score_report(report, "no_fault")
    assert not wrong_top1["top1"] and wrong_top1["top3"]
    assert false_positive["normal_correct"] is False
    summary = summarize([{"scenario_id": "fault", "status": "completed", "score": wrong_top1},
                         {"scenario_id": "normal", "status": "completed", "score": false_positive},
                         {"scenario_id": "failed", "status": "failed", "score": None}])
    assert summary["overall"]["top1"] == {"numerator": 0, "denominator": 1, "rate": 0}
    assert summary["overall"]["no_fault_accuracy"]["denominator"] == 1
    assert summary["overall"]["attempts"] == 3
    assert summary["overall"]["verified_fault_top1"]["denominator"] == 0


def test_recovery_uses_actual_deltas_and_requires_observed_symptom_drop():
    from opspilot.otel_benchmark.runner import recovery_checks

    scenario = load_dataset(DATASET).scenarios[1]
    delta = {"metrics.query.cpu_usage": {"baseline": 0.001, "fault": 0.14}}
    high = recovery_checks(scenario, delta, {"metrics.query": {"cpu_usage": {"current": 0.1}}})
    low = recovery_checks(scenario, delta, {"metrics.query": {"cpu_usage": {"current": 0.002}}})
    assert high["metrics.query.cpu_usage"]["recovered"] is False
    assert low["metrics.query.cpu_usage"]["recovered"] is True


def test_metric_trend_is_computed_from_recorded_time_samples():
    from opspilot.otel_benchmark.runner import metric_trends

    observed = {"metrics.query": {"memory_usage": {"data_points": [
        {"timestamp": 0, "value": 0.8}, {"timestamp": 10, "value": 0.2},
        {"timestamp": 20, "value": 0.3}, {"timestamp": 30, "value": 0.4}]}}}
    trend = metric_trends(observed, datetime.fromtimestamp(10, UTC))["memory_usage"]
    assert trend["samples"] == 3
    assert trend["slope_per_second"] == pytest.approx(0.01)


def test_memory_case_requires_actual_rising_memory_not_just_cpu():
    from opspilot.otel_benchmark.runner import fault_observed

    scenario = load_dataset(DATASET).scenarios[-1]
    assert not fault_observed(scenario, {}, {}, {}, {"cpu_usage": {"samples": 5, "slope_per_second": 1}})
    assert fault_observed(scenario, {}, {}, {}, {"memory_usage": {
        "samples": 5, "slope_per_second": 0.001, "first": 0.5, "last": 0.52}})


async def test_trial_exception_always_resets_faults(tmp_path, monkeypatch):
    from opspilot.otel_benchmark import runner

    resets = []

    class Controller:
        async def reset_all_faults(self):
            resets.append(True)

    async def failure(*args, **kwargs):
        raise RuntimeError("forced telemetry failure")

    async def instant(*args, **kwargs):
        pass

    monkeypatch.setattr(runner, "containers", list)
    monkeypatch.setattr(runner, "locust", failure)
    monkeypatch.setattr(runner, "wait", instant)
    dataset = load_dataset(DATASET)
    result = await runner.trial(dataset, dataset.profiles["smoke"], dataset.scenarios[0], 1,
                                tmp_path / "trial", Controller(), LeakageGuard(["paymentFailure"]))
    assert len(resets) == 2
    assert result["status"] == "failed"
    assert result["score"] is None


async def test_failure_after_enable_resets_active_fault_before_recovery(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from opspilot.otel_benchmark import runner

    state = {"active": False, "enabled": False}

    class Controller:
        async def reset_all_faults(self):
            state["active"] = False

        async def set_fault(self, flag, variant):
            state.update(active=True, enabled=True)

    class Capture:
        def __init__(self, *args):
            self.provider = SimpleNamespace(settings=None)

        def flush(self):
            pass

    async def snapshot(capture, alert, prefix):
        if prefix == "telemetry":
            assert state["active"] is True
            raise RuntimeError("failed backend while injection active")
        assert state["active"] is False
        return {"metrics.query": {"cpu_usage": {"current": 0.01}}, "traces.query": {"traces": [{}]}}, []

    async def load(client, dataset, directory, phase):
        return 1 if phase == "start" else 2

    async def instant(*args, **kwargs):
        pass

    monkeypatch.setattr(runner, "containers", list)
    monkeypatch.setattr(runner, "RuntimeSettings", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "Capture", Capture)
    monkeypatch.setattr(runner, "snapshot", snapshot)
    monkeypatch.setattr(runner, "locust", load)
    monkeypatch.setattr(runner, "wait", instant)
    dataset = load_dataset(DATASET)
    record = await runner.trial(dataset, dataset.profiles["smoke"], dataset.scenarios[1], 1,
                                tmp_path / "trial", Controller(), LeakageGuard(["adHighCpu"]))
    assert state == {"active": False, "enabled": True}
    assert record["status"] == "failed" and record["score"] is None

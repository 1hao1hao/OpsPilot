"""Run actual isolated OTel experiments and retain auditable failures and observations."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import httpx

from opspilot.config import RuntimeSettings
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation.report import build_report
from opspilot.models import ToolCall
from opspilot.observations.provider import OpenTelemetryDemoProvider
from opspilot.tools import ToolExecutor, build_default_registry

from .controller import OpenTelemetryDemoFaultController
from .isolation import LeakageGuard
from .schema import load_dataset, make_alert
from .scoring import score_report, summarize

ROOT = Path(__file__).resolve().parents[3]


def save(directory, name, value):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def command(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8").strip()


def containers():
    names = command("docker", "ps", "-a", "--filter", "label=com.docker.compose.project=opspilot-otel-demo",
                    "--format", "{{.ID}}").splitlines()
    items = json.loads(command("docker", "inspect", *names))
    return [{"name": x["Name"].lstrip("/"), "image": x["Config"]["Image"], "image_id": x["Image"],
             "state": x["State"]["Status"], "oom_killed": x["State"]["OOMKilled"],
             "restart_count": x["RestartCount"], "health": x["State"].get("Health", {}).get("Status"),
             "memory_limit": x["HostConfig"]["Memory"]} for x in items]


class Capture:
    def __init__(self, directory, settings, guard):
        self.directory = directory
        self.guard = guard
        self.provider = OpenTelemetryDemoProvider(settings)
        self.requests = []
        self.arguments = []
        self.count = 0
        for client in (self.provider.prometheus, self.provider.jaeger, self.provider.opensearch):
            original = client.request

            async def request(method, path, _original=original, _backend=client.backend, **kwargs):
                self.count += 1
                ref = f"telemetry/raw/{self.count:05d}_{_backend}.json"
                item = {"backend": _backend, "method": method, "path": path, "arguments": kwargs, "reference": ref}
                try:
                    result = await _original(method, path, **kwargs)
                    save(directory, ref, {**item, "response": result})
                    self.requests.append(item)
                    return result
                except Exception as exc:
                    save(directory, ref, {**item, "error_type": type(exc).__name__, "error": str(exc)})
                    self.requests.append({**item, "error": str(exc)})
                    raise

            client.request = request

    def registry(self):
        registry = build_default_registry(provider=self.provider, settings=self.provider.settings)
        for name in registry.names():
            definition = registry.get(name)
            original = definition.handler

            async def handler(payload, _original=original, _name=name):
                arguments = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
                self.guard.assert_clean(arguments)
                self.arguments.append({"tool": _name, "arguments": arguments})
                original_result = await _original(payload)
                wire_result = original_result.model_dump(mode="json") if hasattr(original_result, "model_dump") else original_result
                result = self.guard.sanitize(wire_result)
                self.guard.assert_clean(result)
                return result

            definition.handler = handler
        return registry

    def flush(self):
        save(self.directory, "telemetry/raw_manifest.json", self.requests)
        save(self.directory, "telemetry/prometheus_queries.json", [r for r in self.requests if r["backend"] == "prometheus"])
        save(self.directory, "rca/tool_arguments.json", self.arguments)
        save(self.directory, "benchmark/isolation_audit.json", {"passed": True, "control_records_removed": self.guard.dropped})


async def snapshot(capture, alert, prefix):
    executor = ToolExecutor(capture.registry())
    results = []
    for name in executor.registry.names():
        result = await executor.execute(ToolCall(tool_call_id=uuid.uuid4().hex, tool_name=name,
                                               arguments={"alert": alert.model_dump(mode="json")}))
        data = result.model_dump(mode="json")
        capture.guard.assert_clean(data)
        results.append(data)
    save(capture.directory, prefix + "/tool_results.json", results)
    observations = {x["tool_name"]: (x.get("data") or {}).get("observations", {}) for x in results}
    save(capture.directory, prefix + "/metrics_normalized.json", observations.get("metrics.query", {}))
    save(capture.directory, prefix + "/logs.json", observations.get("logs.query", {}))
    save(capture.directory, prefix + "/traces.json", observations.get("traces.query", {}))
    return observations, [x for x in results if x["status"] != "success"]


async def locust(client, dataset, directory, phase):
    response = await client.get("http://127.0.0.1:18089/stats/requests")
    response.raise_for_status()
    stats = response.json()
    save(directory, "load/" + phase + ".json", stats)
    if stats["state"] not in ("running", "spawning") or stats["user_count"] != dataset.load["user_count"]:
        raise RuntimeError("Locust is not running at fixed user count")
    return sum(row["num_requests"] for row in stats["stats"] if row["name"] != "Aggregated")


async def wait(seconds, label, directory, deadline=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Fault safety duration exceeded")
        print(f"{label}: {max(0, round(end - time.monotonic()))}s remaining", flush=True)
        await asyncio.sleep(min(15, max(0, end - time.monotonic())))
        if label in ("stabilization", "observation"):
            metadata = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
            if "memory-trend" in metadata["tags"]:
                percent = await asyncio.to_thread(command, "docker", "stats", "email", "--no-stream", "--format", "{{.MemPerc}}")
                fraction = float(percent.replace("%", "")) / 100
                save(directory, f"lifecycle/memory-{time.time_ns()}.json", {"at": datetime.now(UTC).isoformat(), "fraction": fraction})
                if fraction >= metadata["profile"]["max_memory_fraction"]:
                    raise RuntimeError("Memory safety ceiling reached; stop injection and recover, not an OOM success")
    save(directory, "lifecycle/" + label + ".json", {"ended_at": datetime.now(UTC).isoformat(), "seconds": seconds})


def observational_delta(baseline, current):
    output = {}
    for tool, fields in current.items():
        for name, value in fields.items():
            if isinstance(value, dict) and "current" in value:
                previous = baseline.get(tool, {}).get(name, {}).get("current")
                output[tool + "." + name] = {"baseline": previous, "fault": value["current"],
                                             "delta": value["current"] - previous if previous is not None else None}
            elif isinstance(value, (int, float)):
                output[tool + "." + name] = {"baseline": baseline.get(tool, {}).get(name), "fault": value}
    return output


def metric_trends(observations, since):
    output = {}
    for name, value in observations.get("metrics.query", {}).items():
        samples = [point for point in value.get("data_points", []) if point["timestamp"] >= since.timestamp()]
        if len(samples) < 3:
            continue
        xs = [point["timestamp"] - samples[0]["timestamp"] for point in samples]
        ys = [point["value"] for point in samples]
        x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
        denominator = sum((x - x_mean) ** 2 for x in xs)
        output[name] = {"samples": len(samples), "first": ys[0], "last": ys[-1],
                        "slope_per_second": sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / denominator
                        if denominator else None}
    return output


def fault_observed(scenario, delta, baseline, current, trends=None):
    # Controller-only measurement criteria, no changes to Agent predictions/confidence.
    if "memory-trend" in scenario.tags:
        trend = (trends or {}).get("memory_usage", {})
        return bool(trend.get("samples", 0) >= 3 and (trend.get("slope_per_second") or 0) > 0
                    and trend.get("last", 0) - trend.get("first", 0) >= 0.005)
    checks = []
    for tag in scenario.tags:
        if tag == "resource":
            for name in ("cpu_usage", "memory_usage"):
                item = delta.get("metrics.query." + name, {})
                before, after = item.get("baseline"), item.get("fault")
                if before is not None and after is not None:
                    checks.append(after > before + max(abs(before) * 0.2, 0.005))
        if tag == "rpc":
            for key in ("metrics.query.error_rate", "rpc.metrics.error_rate", "rpc.metrics.timeout_rate"):
                item = delta.get(key, {})
                before, after = item.get("baseline"), item.get("fault")
                if before is not None and after is not None:
                    checks.append(after > before)
        if tag == "kafka":
            item = delta.get("kafka.lag.consumer_lag", {})
            checks.append(item.get("fault", 0) > item.get("baseline", 0))
        if tag == "database":
            slow = current.get("db.slowlog", {}).get("slow_query_count", 0)
            checks.append(slow > baseline.get("db.slowlog", {}).get("slow_query_count", 0))
            for name in ("tp95", "tp99"):
                item = delta.get("metrics.query." + name, {})
                if item.get("baseline") is not None:
                    checks.append(item["fault"] > max(item["baseline"] * 1.5, 1000))
    return any(checks) if scenario.fault_control else None


def recovery_checks(scenario, delta, recovered):
    relevant = []
    if "resource" in scenario.tags:
        relevant += ["metrics.query.cpu_usage", "metrics.query.memory_usage"]
    if "rpc" in scenario.tags:
        relevant += ["metrics.query.error_rate", "rpc.metrics.error_rate", "rpc.metrics.timeout_rate"]
    if "kafka" in scenario.tags:
        relevant += ["kafka.lag.consumer_lag"]
    if "database" in scenario.tags:
        relevant += ["metrics.query.tp95", "metrics.query.tp99", "db.slowlog.slow_query_count"]
    checks = {}
    for key in relevant:
        before, during = delta.get(key, {}).get("baseline"), delta.get(key, {}).get("fault")
        tool, field = key.rsplit(".", 1)
        value = recovered.get(tool, {}).get(field)
        after = value.get("current") if isinstance(value, dict) else value
        if before is not None and during is not None and after is not None and during > before:
            limit = before + max((during - before) * 0.25, abs(before) * 0.2, 0.005)
            checks[key] = {"baseline": before, "incident": during, "recovery": after, "limit": limit,
                           "recovered": after <= limit}
    return checks


async def trial(dataset, profile, scenario, index, directory, controller, guard, *, diagnose=None):
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    timing = profile.model_dump()
    timing.update(scenario.timing_overrides)
    save(directory, "scenario.json", {**scenario.model_dump(mode="json"), "upstream_version": dataset.upstream_version,
                                      "profile": timing, "repetition": index})
    save(directory, "benchmark/ground_truth.json", scenario.ground_truth.model_dump(mode="json"))
    record = {"scenario_id": scenario.scenario_id, "repetition": index, "path": str(directory),
              "status": "failed", "score": None}
    controller.save = lambda name, value: save(directory, name, value)
    local_guard = LeakageGuard(guard.flag_names)
    capture = None
    try:
        await controller.reset_all_faults()
        for service in scenario.preparation_restart_services:
            await asyncio.to_thread(command, "docker", "restart", service)
        save(directory, "lifecycle/preparation.json", {"restarted_services": scenario.preparation_restart_services,
                                                       "at": datetime.now(UTC).isoformat()})
        save(directory, "environment.json", {"containers": containers(), "upstream_version": dataset.upstream_version,
                                               "llm_enabled": False, "planner": "existing fallback", "load": dataset.load})
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            count_before = await locust(client, dataset, directory, "start")
            await wait(timing["warmup_seconds"], "baseline", directory)
            baseline_end = datetime.now(UTC) - timedelta(seconds=1)
            settings = RuntimeSettings(llm_enabled=False, observation_backend="otel_demo",
                                       telemetry_window_before_seconds=max(60, timing["warmup_seconds"]),
                                       telemetry_window_after_seconds=1)
            capture = Capture(directory, settings, local_guard)
            baseline_alert = make_alert(scenario.alert_template, baseline_end)
            baseline_deadline = time.monotonic() + timing["baseline_timeout_seconds"]
            attempt = 0
            while True:
                baseline, errors = await snapshot(capture, baseline_alert, f"baseline/attempt-{attempt:02d}")
                if errors:
                    raise RuntimeError("Baseline backend queries failed: " + ",".join(x["tool_name"] for x in errors))
                if baseline.get("metrics.query") and baseline.get("traces.query", {}).get("traces"):
                    break
                if time.monotonic() >= baseline_deadline:
                    raise RuntimeError("Scoped baseline metrics/traces absent after configured bounded wait")
                attempt += 1
                await wait(15, "baseline-samples", directory)
                baseline_end = datetime.now(UTC) - timedelta(seconds=1)
                baseline_alert = make_alert(scenario.alert_template, baseline_end)
            save(directory, "baseline/alert.json", baseline_alert.model_dump(mode="json"))
            save(directory, "baseline/observations.json", baseline)
            enabled_at = datetime.now(UTC)
            if scenario.fault_control:
                await controller.set_fault(scenario.fault_control.flag, scenario.fault_control.variant)
            deadline = time.monotonic() + max(600, timing["fault_stabilization_seconds"] + timing["observation_window_seconds"] + 300)
            await wait(timing["fault_stabilization_seconds"] if scenario.fault_control else 0, "stabilization", directory, deadline)
            event_at = datetime.now(UTC)
            await wait(timing["observation_window_seconds"], "observation", directory, deadline)
            count_after = await locust(client, dataset, directory, "incident")
            if count_after <= count_before:
                raise RuntimeError("Locust request counter did not advance")
            event = make_alert(scenario.alert_template, event_at)
            local_guard.assert_clean(event.model_dump(mode="json"))
            save(directory, "alert.json", event.model_dump(mode="json"))
            before_window = max(60, int((event_at - baseline_end).total_seconds()) + timing["warmup_seconds"])
            capture.provider.settings = RuntimeSettings(llm_enabled=False, observation_backend="otel_demo",
                                                       telemetry_window_before_seconds=before_window,
                                                       telemetry_window_after_seconds=timing["observation_window_seconds"])
            observed, errors = await snapshot(capture, event, "telemetry")
            observed_at = datetime.now(UTC)
            delta = observational_delta(baseline, observed)
            trends = metric_trends(observed, enabled_at)
            record["fault_observed"] = fault_observed(scenario, delta, baseline, observed, trends)
            save(directory, "benchmark/observed_delta.json", {"enabled_at": enabled_at.isoformat(), "delta": delta,
                                                              "trends": trends,
                                                              "fault_observed": record["fault_observed"]})
            if diagnose is None:
                workflow = OpsPilotWorkflow(capture.registry(), settings=capture.provider.settings, execution_mode="sequential")
                rca_start = datetime.now(UTC)
                trace_id = uuid.uuid4().hex
                outcome = await workflow.observe(event, trace_id=trace_id)
                rationale, used = await workflow.root_cause_agent.explain_existing(event, outcome.provisional_candidates, outcome.evidence)
                report = build_report(alert=event, outcome=outcome, trace_id=trace_id,
                                      started_at=rca_start, rationale=rationale, llm_used=used).model_dump(mode="json")
            else:
                report, outcome = await diagnose(capture, event)
            local_guard.assert_clean(report)
            save(directory, "rca/report.json", report)
            save(directory, "rca/events.json", outcome.trace.model_dump(mode="json"))
            save(directory, "rca/tool_results.json", [r.model_dump(mode="json") for r in outcome.tool_results])
            save(directory, "rca/evidence.json", [r.model_dump(mode="json") for r in outcome.evidence])
            record["score"] = score_report(report, scenario.ground_truth.root_cause_type.value)
            save(directory, "benchmark/score.json", record["score"])
            record["status"] = "completed" if not errors else "query_errors"
            save(directory, "benchmark/telemetry_errors.json", errors)
    except Exception as exc:  # noqa: BLE001 - trial failures must be recorded before unconditional reset
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        print(f"Trial error: {type(exc).__name__}: {exc}", flush=True)
    finally:
        await controller.reset_all_faults()
        if capture:
            capture.flush()
        for service in scenario.recovery_restart_services:
            await asyncio.to_thread(command, "docker", "restart", service)
        await wait(timing["cooldown_seconds"], "cooldown", directory)
        final = containers()
        save(directory, "recovery/containers.json", final)
        record["recovery_health"] = all(x["state"] == "running" and x["health"] not in ("unhealthy", "starting") for x in final)
        if capture:
            capture.provider.settings = RuntimeSettings(llm_enabled=False, observation_backend="otel_demo",
                                                       telemetry_window_before_seconds=max(60, timing["cooldown_seconds"]),
                                                       telemetry_window_after_seconds=1)
            recovery_event = make_alert(scenario.alert_template, datetime.now(UTC) - timedelta(seconds=1))
            recovered, recovery_errors = await snapshot(capture, recovery_event, "recovery")
            save(directory, "recovery/observed_delta.json", observational_delta(baseline, recovered) if "baseline" in locals() else {})
            record["recovery_queries_success"] = not recovery_errors
            if "delta" in locals():
                checks = recovery_checks(scenario, delta, recovered)
                recovery_deadline = time.monotonic() + timing["recovery_timeout_seconds"]
                attempt = 0
                while checks and not all(c["recovered"] for c in checks.values()) and time.monotonic() < recovery_deadline:
                    attempt += 1
                    save(directory, f"recovery/check-{attempt:02d}.json", checks)
                    await wait(15, "recovery-poll", directory)
                    recovery_event = make_alert(scenario.alert_template, datetime.now(UTC) - timedelta(seconds=1))
                    recovered, recovery_errors = await snapshot(capture, recovery_event, f"recovery/poll-{attempt:02d}")
                    checks = recovery_checks(scenario, delta, recovered)
                save(directory, "recovery/final_checks.json", checks)
                record["symptom_recovery_confirmed"] = all(c["recovered"] for c in checks.values()) if checks else None
                if record["symptom_recovery_confirmed"] is False:
                    record["status"] = "recovery_failed"
            capture.flush()
        record["duration_seconds"] = time.monotonic() - started
        save(directory, "benchmark/trial_status.json", record)
    if (record["status"] == "completed" and record["recovery_health"] and record.get("recovery_queries_success")
            and record.get("symptom_recovery_confirmed") is not False):
        fixture_path = ROOT / "benchmarks/datasets/otel_demo/v1/fixtures" / scenario.scenario_id / directory.parent.parent.name
        if not (fixture_path / "metadata.json").exists():
            save(fixture_path, "observations.json", local_guard.recorded_fixture(observed))
            save(fixture_path, "metadata.json", {"source": "recorded from OTel Demo", "upstream_commit": dataset.upstream_version["commit"],
                                                  "captured_at": observed_at.isoformat(), "scenario": scenario.scenario_id,
                                                  "artifact": str(directory), "fault_observed": record.get("fault_observed"),
                                                  "lifecycle_complete": True,
                                                  "redaction": "emails and explicit control disclosures removed"})
    print(f"TRIAL {scenario.scenario_id} #{index}: {record['status']}, observed={record.get('fault_observed')}, score={record.get('score')}", flush=True)
    return record


async def run(args):
    dataset_path = Path(args.dataset)
    dataset = load_dataset(dataset_path)
    clone = ROOT / ".external/opentelemetry-demo"
    if command("git", "-C", str(clone), "rev-parse", "HEAD") != dataset.upstream_version["commit"]:
        raise RuntimeError("Upstream commit mismatch")
    if command("git", "-C", str(clone), "status", "--short"):
        raise RuntimeError("Upstream tracked/untracked source is dirty")
    # Reject mounts into tracked source; use directory copy to survive atomic file replacements.
    for name in ("flagd", "flagd-ui"):
        mounts = json.loads(command("docker", "inspect", name))[0]["Mounts"]
        for mount in mounts:
            if mount["Destination"] in ("/etc/flagd", "/app/data"):
                source = Path(mount["Source"]).resolve()
                if source.is_relative_to(clone.resolve()):
                    raise RuntimeError("Flag controls must use an isolated runtime directory")
    initial = containers()
    if len(initial) != 28 or any(x["state"] != "running" or x["health"] in ("unhealthy", "starting") for x in initial):
        raise RuntimeError("All full Demo services must be healthy/running")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    output = ROOT / "artifacts/otel_demo_benchmark" / run_id
    output.mkdir(parents=True)
    print(f"RUN_ID={run_id}", flush=True)
    save(output, "dataset.json", dataset.model_dump(mode="json"))
    sources = {}
    for source in (ROOT / "src/opspilot").rglob("*.py"):
        relative = source.relative_to(ROOT)
        content = source.read_bytes()
        sources[relative.as_posix()] = hashlib.sha256(content).hexdigest()
        archive = output / "source" / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(content)
    save(output, "source_manifest.json", sources)
    save(output, "environment.json", {"docker_version": command("docker", "--version"),
                                      "compose_version": command("docker", "compose", "version"), "containers": initial,
                                      "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                                      "profile": args.profile, "repeat_override": args.repeat,
                                      "source_head": command("git", "rev-parse", "HEAD")})
    load_config = json.loads(command("docker", "inspect", "load-generator"))[0]["Config"]["Env"]
    allowed_load_keys = {"LOCUST_USERS", "LOCUST_SPAWN_RATE", "LOCUST_HOST", "LOCUST_HTTP_USER_WEIGHT",
                         "LOCUST_BROWSER_USER_WEIGHT", "LOCUST_BROWSER_TRAFFIC_ENABLED", "LOCUST_AUTOSTART", "LOCUST_HEADLESS"}
    save(output, "load_config.json", {"requested": dataset.load,
                                      "container_env": {k: v for item in load_config for k, v in [item.split("=", 1)] if k in allowed_load_keys}})
    save(output, "python_environment.json", {"python": platform.python_version(), "system": platform.platform(),
                                             "packages": {name: version(name) for name in ("httpx", "pydantic", "pydantic-settings", "PyYAML", "numpy")}})
    controller = OpenTelemetryDemoFaultController(lambda name, value: save(output, name, value))
    state = await controller.read_current_flags()
    guard = LeakageGuard(state["flags"].keys())
    profile = dataset.profiles[args.profile]
    records = []
    try:
        await controller.reset_all_faults()
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post("http://127.0.0.1:18089/swarm", data={"user_count": dataset.load["user_count"],
                                                                              "spawn_rate": dataset.load["spawn_rate"]})
            response.raise_for_status()
            if not response.json().get("success"):
                raise RuntimeError("Cannot configure fixed Locust load")
        for scenario in dataset.scenarios:
            if args.scenario and scenario.scenario_id not in args.scenario:
                continue
            repetitions = profile.normal_repeat if not scenario.fault_control else args.repeat or profile.repeat
            for index in range(1, repetitions + 1):
                directory = output / scenario.scenario_id / f"repeat-{index:02d}"
                records.append(await trial(dataset, profile, scenario, index, directory, controller, guard))
                save(output, "trials.json", records)
                save(output, "summary.json", summarize(records))
                if not records[-1]["recovery_health"] or records[-1].get("symptom_recovery_confirmed") is False:
                    raise RuntimeError("Recovery not confirmed; stop to avoid contaminating the next case")
    finally:
        controller.save = lambda name, value: save(output, "final/" + name, value)
        await controller.reset_all_faults()
        save(output, "final/flags.json", await controller.read_current_flags())
        save(output, "trials.json", records)
        save(output, "summary.json", summarize(records))
    print(json.dumps(summarize(records), indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT / "benchmarks/datasets/otel_demo/v1/scenarios.yaml"))
    parser.add_argument("--profile", choices=("smoke", "release"), default="smoke")
    parser.add_argument("--repeat", type=int, choices=range(1, 101))
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    lock = ROOT / ".external/otel-benchmark.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("Benchmark controller lock exists; verify no runner is active before removing it") from None
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(str(os.getpid()))
        asyncio.run(run(args))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

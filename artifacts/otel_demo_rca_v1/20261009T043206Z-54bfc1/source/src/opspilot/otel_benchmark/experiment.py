"""Frozen paired release: 21 lifecycle trials and 63 actual mode investigations."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from opspilot.config import RuntimeSettings
from opspilot.llm import DeepSeekSecrets

from .controller import OpenTelemetryDemoFaultController
from .isolation import LeakageGuard
from .modes import MODES, mode_order, read_json, run_mode
from .runner import ROOT, command, containers, save, trial
from .schema import load_dataset
from .scoring import score_report


def assert_frozen(output):
    changed = [name for name, digest in read_json(output / "source_manifest.json").items()
               if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    if changed:
        raise RuntimeError("Frozen files changed: " + ", ".join(changed))
    frozen_path = output / "frozen_configuration.json"
    if frozen_path.exists():
        changed = [name for name, digest in read_json(frozen_path).items()
                   if hashlib.sha256((output / name).read_bytes()).hexdigest() != digest]
        if changed:
            raise RuntimeError("Frozen artifact configuration changed: " + ", ".join(changed))


def freeze(output, config_path, config, dataset_path, dataset):
    runtime = RuntimeSettings()
    config["llm"].update(model=runtime.llm_model, base_url=runtime.llm_base_url,
                          timeout_seconds=runtime.llm_timeout_seconds)
    save(output, "config.json", config)
    save(output, "dataset.json", dataset.model_dump(mode="json"))
    manifest = {}
    sources = list((ROOT / "src/opspilot").rglob("*.py")) + [dataset_path, config_path]
    for source in sources:
        relative = source.relative_to(ROOT)
        content = source.read_bytes()
        manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
        archived = output / "source" / relative
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(content)
    save(output, "source_manifest.json", manifest)
    save(output, "environment.json", {"upstream": dataset.upstream_version, "opspilot_commit": command("git", "rev-parse", "HEAD"),
                                      "working_tree_snapshot": "source_manifest.json; preserved uncommitted source is authoritative",
                                      "docker_version": command("docker", "--version"),
                                      "compose_version": command("docker", "compose", "version"), "containers": containers(),
                                      "python": platform.python_version(), "os": platform.platform(),
                                      "frozen_at": datetime.now(UTC).isoformat(), "requested_diagnoses": len(dataset.scenarios) * config["repeats"] * len(MODES)})
    save(output, "frozen_configuration.json", {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                               for name in ("config.json", "dataset.json", "environment.json", "source_manifest.json")})
    return config


def mode_settings(base, config, mode):
    fields = base.model_dump()
    fields.update(config["rca"], llm_enabled=mode != "fixed_full", llm_model=config["llm"]["model"],
                  llm_base_url=config["llm"]["base_url"], llm_timeout_seconds=config["llm"]["timeout_seconds"])
    if mode == "adaptive_no_l2":
        fields["investigation_max_expert_calls"] = 0
    return RuntimeSettings(_env_file=None, **fields)


async def run(args):
    config_path = (ROOT / args.config).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config["repeats"] < 3 or tuple(config["modes"]) != MODES or config["profile"] != "release":
        raise ValueError("Release requires the three named modes and at least three actual repeats")
    dataset_path = ROOT / config["dataset"]
    dataset = load_dataset(dataset_path)
    clone = ROOT / ".external/opentelemetry-demo"
    if command("git", "-C", str(clone), "rev-parse", "HEAD") != dataset.upstream_version["commit"] or command("git", "-C", str(clone), "status", "--short"):
        raise RuntimeError("Pinned upstream commit must remain clean")
    initial = containers()
    if len(initial) != 28 or any(x["state"] != "running" or x["health"] in ("starting", "unhealthy") for x in initial):
        raise RuntimeError("All 28 full Demo services must be healthy/running")
    for name in ("flagd", "flagd-ui"):
        for mount in json.loads(command("docker", "inspect", name))[0]["Mounts"]:
            if mount["Destination"] in ("/etc/flagd", "/app/data") and Path(mount["Source"]).resolve().is_relative_to(clone.resolve()):
                raise RuntimeError("Flag controls must use the isolated runtime copy")
    if not DeepSeekSecrets().deepseek_api_key.get_secret_value().strip():
        raise RuntimeError("A real LLM API key is required")
    if args.resume:
        output = args.resume.resolve()
        assert_frozen(output)
        config = read_json(output / "config.json")
        dataset = load_dataset(output / "source" / config["dataset"])
        pairs = read_json(output / "pairs.json")
        records = read_json(output / "records.json")
    else:
        output = ROOT / "artifacts/otel_demo_rca_v1" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
        output.mkdir(parents=True)
        config = freeze(output, config_path, config, dataset_path, dataset)
        pairs, records = [], []
    print(f"EXPERIMENT_ID={output.name}", flush=True)
    controller = OpenTelemetryDemoFaultController(lambda name, value: save(output, name, value))
    guard = LeakageGuard((await controller.read_current_flags())["flags"].keys())
    save(output, "status.json", {"status": "running", "started_at": datetime.now(UTC).isoformat()})
    try:
        await controller.reset_all_faults()
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post("http://127.0.0.1:18089/swarm", data={"user_count": dataset.load["user_count"], "spawn_rate": dataset.load["spawn_rate"]})
            response.raise_for_status()
            if not response.json().get("success"):
                raise RuntimeError("Official Locust swarm configuration failed")
        for scenario in dataset.scenarios:
            for repetition in range(1, config["repeats"] + 1):
                if any(p["scenario_id"] == scenario.scenario_id and p["repetition"] == repetition
                       and p["status"] == "completed" and p.get("recovery_health") and p.get("recovery_queries_success")
                       and p.get("symptom_recovery_confirmed") is not False for p in pairs):
                    continue
                assert_frozen(output)
                directory = output / scenario.scenario_id / f"repeat-{repetition:02d}"
                attempt = 1
                while directory.exists():
                    attempt += 1
                    directory = output / scenario.scenario_id / f"repeat-{repetition:02d}-attempt-{attempt:02d}"
                completed_modes = []

                async def diagnose(capture, event, *, directory=directory, repetition=repetition,
                                   scenario=scenario, completed_modes=completed_modes):
                    outcomes = {}
                    save(directory, "mode_order.json", list(mode_order(repetition)))
                    for mode in mode_order(repetition):
                        mode_directory = directory / "modes" / mode
                        if "memory-trend" in scenario.tags:
                            fraction = float(command("docker", "stats", "email", "--no-stream", "--format", "{{.MemPerc}}").replace("%", "")) / 100
                            save(directory, f"lifecycle/memory-before-{mode}.json", {"fraction": fraction})
                            if fraction >= dataset.profiles["release"].max_memory_fraction:
                                raise RuntimeError("Memory safety ceiling reached before mode; recover")
                        report, outcome = await run_mode(mode, mode_directory, event,
                                                         mode_settings(capture.provider.settings, config, mode), guard.flag_names)
                        score = score_report(report, scenario.ground_truth.root_cause_type.value)
                        save(mode_directory, "benchmark/score.json", score)
                        completed_modes.append({"scenario_id": scenario.scenario_id, "repetition": repetition, "mode": mode,
                                                "path": str(mode_directory), "score": score, "llm": read_json(mode_directory / "llm/metrics.json")})
                        outcomes[mode] = report, outcome
                    return outcomes["full_adaptive"]

                pair = await trial(dataset, dataset.profiles["release"], scenario, repetition, directory, controller, guard, diagnose=diagnose)
                pairs.append(pair)
                for record in completed_modes:
                    record.update(status=pair["status"], fault_observed=pair.get("fault_observed"),
                                  recovery_health=pair.get("recovery_health"), recovery_queries_success=pair.get("recovery_queries_success"),
                                  symptom_recovery_confirmed=pair.get("symptom_recovery_confirmed"), lifecycle_path=str(directory))
                    records.append(record)
                save(output, "pairs.json", pairs)
                save(output, "records.json", records)
                print(f"PROGRESS paired_lifecycles={len(pairs)}/21, actual_diagnoses={len(records)}/63", flush=True)
                if pair["status"] != "completed" or not pair["recovery_health"] or not pair.get("recovery_queries_success") or pair.get("symptom_recovery_confirmed") is False:
                    raise RuntimeError("Trial or recovery incomplete; stop instead of contaminating the next pair")
        assert_frozen(output)
        save(output, "status.json", {"status": "completed", "finished_at": datetime.now(UTC).isoformat(), "actual_diagnoses": len(records), "actual_lifecycles": len(pairs)})
    except Exception as exc:
        save(output, "status.json", {"status": "interrupted", "error_type": type(exc).__name__, "error": str(exc), "at": datetime.now(UTC).isoformat()})
        raise
    finally:
        controller.save = lambda name, value: save(output, "final/" + name, value)
        await controller.reset_all_faults()
        save(output, "final/flags.json", await controller.read_current_flags())
        save(output, "final/containers.json", containers())
        save(output, "pairs.json", pairs)
        save(output, "records.json", records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="benchmarks/datasets/otel_demo/v1/experiment.yaml")
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    lock = ROOT / ".external/otel-benchmark.lock"
    lock.parent.mkdir(exist_ok=True)
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("Another controller lock exists; verify the owning process") from None
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(str(os.getpid()))
        asyncio.run(run(args))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

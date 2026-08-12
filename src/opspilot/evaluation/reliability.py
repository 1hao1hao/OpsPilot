"""Repeatable runtime fault suite over real PostgreSQL, Redis and worker processes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import yaml
from pydantic import Field, model_validator

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent, RunStatus
from opspilot.models.schemas import StrictModel
from opspilot.persistence import Database
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.faults import RuntimeFaultType
from opspilot.runtime.queue import RedisRunQueue
from opspilot.runtime.task_manager import TaskManager


class RuntimeFaultCase(StrictModel):
    case_id: str
    fault_type: RuntimeFaultType
    trials: int = Field(ge=1, le=100)
    injection_point: str
    failure_count: int = Field(default=1, ge=1, le=10)
    expected_status: RunStatus = RunStatus.SUCCEEDED
    expected_degraded: bool = False


class ReliabilityConfig(StrictModel):
    schema_version: str = "1.0"
    name: str
    database_url_env: str = "OPSPILOT_TEST_DATABASE_URL"
    redis_url_env: str = "OPSPILOT_TEST_REDIS_URL"
    artifact_root: str = "artifacts/evaluations"
    graph_version: str = "opspilot-runtime-v1"
    config_version: str = "runtime-faults-v1"
    cases: list[RuntimeFaultCase]

    @model_validator(mode="after")
    def contains_minimum_fault_matrix(self) -> ReliabilityConfig:
        expected = set(RuntimeFaultType)
        actual = {case.fault_type for case in self.cases}
        if actual != expected:
            missing = sorted(item.value for item in expected - actual)
            extra = sorted(item.value for item in actual - expected)
            raise ValueError(f"fault matrix mismatch: missing={missing}, extra={extra}")
        if len(self.cases) != len(expected):
            raise ValueError("each minimum fault type must appear exactly once")
        return self


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def compute_reliability_metrics(trials: list[dict]) -> dict:
    total = len(trials)
    recovered = sum(bool(trial["recovery_success"]) for trial in trials)
    e2e = sum(bool(trial["e2e_success"]) for trial in trials)
    duplicate_executions = sum(int(trial["duplicate_successful_tool_executions"]) for trial in trials)
    recovery_latencies = [
        float(trial["recovery_latency_ms"])
        for trial in trials
        if isinstance(trial.get("recovery_latency_ms"), (int, float))
    ]
    e2e_latencies = [float(trial["e2e_latency_ms"]) for trial in trials]
    by_fault: dict[str, dict] = {}
    for fault_type in RuntimeFaultType:
        subset = [trial for trial in trials if trial["fault_type"] == fault_type.value]
        successes = sum(bool(trial["e2e_success"]) for trial in subset)
        by_fault[fault_type.value] = {
            "success_rate": {
                "value": round(successes / len(subset), 6) if subset else None,
                "numerator": successes,
                "denominator": len(subset),
            }
        }
    return {
        "trial_count": total,
        "recovery_success_rate": {
            "value": round(recovered / total, 6) if total else None,
            "numerator": recovered,
            "denominator": total,
        },
        "e2e_success_rate": {
            "value": round(e2e / total, 6) if total else None,
            "numerator": e2e,
            "denominator": total,
        },
        "duplicate_successful_tool_executions": {
            "value": duplicate_executions,
            "denominator": sum(int(trial["successful_tool_executions"]) for trial in trials),
        },
        "p95_recovery_latency_ms": _percentile(recovery_latencies, 0.95),
        "recovery_latency_trial_count": len(recovery_latencies),
        "p95_e2e_latency_ms": _percentile(e2e_latencies, 0.95),
        "by_fault_type": by_fault,
    }


async def _worker_process(settings: RuntimeSettings, *arguments: str, extra_env: dict[str, str] | None = None) -> int:
    environment = {
        **os.environ,
        "OPSPILOT_DATABASE_URL": settings.database_url,
        "OPSPILOT_REDIS_URL": settings.redis_url,
        "OPSPILOT_QUEUE_NAME": settings.queue_name,
        "OPSPILOT_GRAPH_VERSION": settings.graph_version,
        "OPSPILOT_CONFIG_VERSION": settings.config_version,
        "OPSPILOT_RECOVERY_STALE_SECONDS": "0",
        "OPSPILOT_QUEUE_REPAIR_SECONDS": "30",
        "OPSPILOT_TOOL_TIMEOUT_SECONDS": str(settings.tool_timeout_seconds),
        "OPSPILOT_TOOL_MAX_ATTEMPTS": str(settings.tool_max_attempts),
        "OPSPILOT_RETRY_BACKOFF_SECONDS": str(settings.retry_backoff_seconds),
        **(extra_env or {}),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "opspilot.runtime.worker",
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode not in {0, 97}:
        raise RuntimeError(f"worker exited {process.returncode}: {stderr.decode(errors='replace')[-1000:]}")
    return int(process.returncode)


def _alert(trial_id: str) -> AlertEvent:
    return AlertEvent(
        alert_id=f"alert-{trial_id}",
        service_name="checkout-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        description="fixed reliability scenario",
        signals={"db": {"replication_lag_seconds": 20}, "metric": {"cpu_usage": 0.55}},
    )


async def _execute_trial(
    *,
    evaluation_id: str,
    case: RuntimeFaultCase,
    trial_number: int,
    database_url: str,
    redis_url: str,
    graph_version: str,
    config_version: str,
) -> tuple[dict, list[dict]]:
    trial_id = f"{case.case_id}-{trial_number:02d}"
    queue_name = f"opspilot:reliability:{evaluation_id}:{trial_id}"
    settings = RuntimeSettings(
        database_url=database_url,
        redis_url=redis_url,
        queue_name=queue_name,
        graph_version=graph_version,
        config_version=config_version,
        recovery_stale_seconds=0,
        tool_timeout_seconds=0.03,
        tool_max_attempts=2,
        retry_backoff_seconds=0.005,
    )
    database = Database(database_url)
    repository = RuntimeRepository(database.sessions)
    queue = RedisRunQueue.from_url(redis_url, queue_name)
    manager = TaskManager(repository, queue, settings)
    request_id = f"{evaluation_id}:{trial_id}"
    started = perf_counter()
    worker_exit_codes: list[int] = []
    try:
        if case.fault_type == RuntimeFaultType.DUPLICATE_REQUEST:
            accepted = await asyncio.gather(
                *(manager.create_run(request_id=request_id, alert=_alert(trial_id)) for _ in range(8))
            )
            run_ids = {item.run_id for item in accepted}
            run_id = accepted[0].run_id
        else:
            accepted_run = await manager.create_run(request_id=request_id, alert=_alert(trial_id))
            run_id = accepted_run.run_id
            run_ids = {run_id}

        if case.fault_type == RuntimeFaultType.DUPLICATE_DELIVERY:
            await queue.enqueue(run_id)

        if case.fault_type == RuntimeFaultType.WORKER_CRASH:
            worker_exit_codes.append(
                await _worker_process(
                    settings,
                    "--once",
                    extra_env={"OPSPILOT_CRASH_BEFORE_STEP": case.injection_point},
                )
            )
            worker_exit_codes.append(await _worker_process(settings, "--recover-once"))
            worker_exit_codes.append(await _worker_process(settings, "--once"))
        else:
            fault_env = {}
            if case.fault_type in {RuntimeFaultType.TOOL_TIMEOUT, RuntimeFaultType.TOOL_HTTP_500}:
                fault_env = {
                    "OPSPILOT_FAULT_TYPE": case.fault_type.value,
                    "OPSPILOT_FAULT_TARGET_SIGNAL": case.injection_point,
                    "OPSPILOT_FAULT_COUNT": str(case.failure_count),
                }
            worker_exit_codes.append(await _worker_process(settings, "--once", extra_env=fault_env))
            if case.fault_type == RuntimeFaultType.DUPLICATE_DELIVERY:
                worker_exit_codes.append(await _worker_process(settings, "--once"))

        run = await manager.get_run(run_id)
        result = await manager.get_result(run_id)
        events = await manager.get_events(run_id) or []
        event_rows = [event.model_dump(mode="json") for event in events]
        success_call_ids = [
            event.detail.get("tool_call_id")
            for event in events
            if event.event_type == "tool.finished" and event.status == "SUCCEEDED"
        ]
        duplicate_successes = len(success_call_ids) - len(set(success_call_ids))
        report_valid = result is not None and result.report is not None
        status_matches = run is not None and run.status == case.expected_status
        degraded_matches = report_valid and result.report.degraded == case.expected_degraded
        crash_process_valid = case.fault_type != RuntimeFaultType.WORKER_CRASH or worker_exit_codes[:1] == [97]
        unique_run_valid = case.fault_type != RuntimeFaultType.DUPLICATE_REQUEST or len(run_ids) == 1
        recovery_success = all(
            [status_matches, report_valid, degraded_matches, crash_process_valid, unique_run_valid, duplicate_successes == 0]
        )
        recovered_event = next((event for event in events if event.event_type == "run.recovered"), None)
        succeeded_event = next((event for event in events if event.event_type == "run.succeeded"), None)
        recovery_latency_ms = None
        if recovered_event and succeeded_event:
            recovery_latency_ms = max(
                (succeeded_event.created_at - recovered_event.created_at).total_seconds() * 1000,
                0,
            )
        failure_type = None
        if not recovery_success:
            if not status_matches:
                failure_type = "terminal_state"
            elif not report_valid:
                failure_type = "report_missing"
            elif not degraded_matches:
                failure_type = "degraded_contract"
            elif duplicate_successes:
                failure_type = "duplicate_execution"
            else:
                failure_type = "fault_contract"
        trial = {
            "trial_id": trial_id,
            "case_id": case.case_id,
            "fault_type": case.fault_type.value,
            "injection_point": case.injection_point,
            "failure_count": case.failure_count,
            "run_id": run_id,
            "request_id": request_id,
            "final_status": run.status.value if run else "MISSING",
            "expected_status": case.expected_status.value,
            "degraded": result.report.degraded if report_valid else None,
            "expected_degraded": case.expected_degraded,
            "report_valid": report_valid,
            "recovered_count": run.recovered_count if run else 0,
            "worker_exit_codes": worker_exit_codes,
            "successful_tool_executions": len(success_call_ids),
            "duplicate_successful_tool_executions": duplicate_successes,
            "recovery_success": recovery_success,
            "e2e_success": recovery_success,
            "recovery_latency_ms": round(recovery_latency_ms, 3) if recovery_latency_ms is not None else None,
            "e2e_latency_ms": round((perf_counter() - started) * 1000, 3),
            "failure_type": failure_type,
        }
        return trial, event_rows
    finally:
        await queue.client.delete(queue_name)
        await queue.close()
        await database.dispose()


async def run_reliability(config_path: str | Path) -> Path:
    config_path = Path(config_path)
    config_bytes = config_path.read_bytes()
    config = ReliabilityConfig.model_validate(yaml.safe_load(config_bytes))
    database_url = os.getenv(config.database_url_env)
    redis_url = os.getenv(config.redis_url_env)
    if not database_url or not redis_url:
        raise RuntimeError(
            f"reliability requires {config.database_url_env} and {config.redis_url_env}; "
            "run against isolated PostgreSQL and Redis services"
        )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evaluation_id = f"{timestamp}-{config.name}"
    output_root = Path(config.artifact_root) / evaluation_id
    output_root.mkdir(parents=True, exist_ok=False)
    timeline_root = output_root / "trials"
    timeline_root.mkdir()
    trials: list[dict] = []
    failures: list[dict] = []
    for case in config.cases:
        for trial_number in range(1, case.trials + 1):
            trial, events = await _execute_trial(
                evaluation_id=evaluation_id,
                case=case,
                trial_number=trial_number,
                database_url=database_url,
                redis_url=redis_url,
                graph_version=config.graph_version,
                config_version=config.config_version,
            )
            trials.append(trial)
            if not trial["recovery_success"]:
                failures.append(trial)
            _write_jsonl(timeline_root / f"{trial['trial_id']}.jsonl", events)
    metrics = compute_reliability_metrics(trials)
    manifest = {
        "evaluation_id": evaluation_id,
        "created_at": datetime.now(UTC).isoformat(),
        "suite": config.name,
        "schema_version": config.schema_version,
        "config_path": str(config_path),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "graph_version": config.graph_version,
        "runtime_config_version": config.config_version,
        "fault_case_count": len(config.cases),
        "trial_count": len(trials),
        "command": " ".join(sys.argv),
        "python": platform.python_version(),
        "external_paid_api": False,
        "service_note": "real PostgreSQL, Redis and independent worker subprocesses",
    }
    _write_json(output_root / "manifest.json", manifest)
    _write_json(output_root / "reliability.json", metrics)
    _write_jsonl(output_root / "trials.jsonl", trials)
    _write_jsonl(output_root / "failures.jsonl", failures)
    (output_root / "report.md").write_text(_render_report(manifest, metrics, failures), encoding="utf-8")
    return output_root


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _render_report(manifest: dict, metrics: dict, failures: list[dict]) -> str:
    recovery = metrics["recovery_success_rate"]
    e2e = metrics["e2e_success_rate"]
    duplicate = metrics["duplicate_successful_tool_executions"]
    failure_ids = ", ".join(item["trial_id"] for item in failures) or "none"
    return f"""# Runtime Reliability Report

- Evaluation: `{manifest['evaluation_id']}`
- Fault cases/trials: {manifest['fault_case_count']} / {manifest['trial_count']}
- Runtime config: `{manifest['runtime_config_version']}`
- Paid external API: no

## Metrics

- Recovery Success Rate: {recovery['value']:.3f} ({recovery['numerator']}/{recovery['denominator']})
- E2E Success Rate: {e2e['value']:.3f} ({e2e['numerator']}/{e2e['denominator']})
- Duplicate successful ToolExecution: {duplicate['value']} / {duplicate['denominator']}
- P95 recovery latency: {metrics['p95_recovery_latency_ms']} ms ({metrics['recovery_latency_trial_count']} trials)
- P95 E2E latency: {metrics['p95_e2e_latency_ms']} ms

## Failed trials

{failure_ids}

Every trial remains in `trials.jsonl`; every event timeline is stored under `trials/`.
"""

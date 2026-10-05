"""Capture real backend responses through the existing integration and RCA engine."""

import argparse
import asyncio
import contextvars
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from opspilot.config import RuntimeSettings
from opspilot.graph import OpsPilotWorkflow
from opspilot.investigation.report import build_report
from opspilot.models import AlertEvent, ToolCall
from opspilot.observations.provider import OpenTelemetryDemoProvider
from opspilot.tools import ToolExecutor, build_default_registry

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/otel_demo/live_acceptance/local_windows"
SCOPE = contextvars.ContextVar("capture_scope", default="discovery")


def save(name, value):
    path = OUTPUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def has_observations(value):
    if isinstance(value, dict):
        return any(has_observations(item) for item in value.values())
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""


class Capture:
    def __init__(self, phase):
        self.phase = phase
        self.references = {}
        self.counter = 0
        self.provider = OpenTelemetryDemoProvider(RuntimeSettings(llm_enabled=False))
        for client in (self.provider.prometheus, self.provider.jaeger, self.provider.opensearch):
            original = client.request

            async def request(method, path, _original=original, _backend=client.backend, **kwargs):
                self.counter += 1
                reference = f"{self.phase}/raw/{self.counter:04d}_{_backend}.json"
                self.references.setdefault(SCOPE.get(), []).append(reference)
                record = {"backend": _backend, "method": method, "path": path, "arguments": kwargs}
                try:
                    payload = await _original(method, path, **kwargs)
                    record["response"] = payload
                    save(reference, record)
                    return payload
                except Exception as exc:
                    record["error"] = {"type": type(exc).__name__, "message": str(exc)}
                    save(reference, record)
                    raise

            client.request = request

    def registry(self):
        registry = build_default_registry(provider=self.provider, settings=self.provider.settings)
        for name in registry.names():
            definition = registry.get(name)
            original = definition.handler

            async def handler(payload, _original=original, _name=name):
                token = SCOPE.set(_name)
                try:
                    return await _original(payload)
                finally:
                    SCOPE.reset(token)

            definition.handler = handler
        return registry


def alert(service, kind="custom", age=45):
    return AlertEvent(
        alert_id=f"live-{service}-{datetime.now(UTC).strftime('%H%M%S')}",
        service_name=service,
        alert_type=kind,
        severity="P2",
        timestamp=datetime.now(UTC) - timedelta(seconds=age),
        description="Service CPU utilization increased during ongoing request traffic",
    )


async def snapshot(phase, service):
    capture = Capture(phase)
    registry = capture.registry()
    executor = ToolExecutor(registry)
    event = alert(service)
    save(f"{phase}/Alert.json", event.model_dump(mode="json"))
    output = {}
    for name in registry.names():
        result = await executor.execute(ToolCall(tool_call_id=f"{phase}-{name}", tool_name=name,
                                               arguments={"alert": event.model_dump(mode="json")}))
        data = result.model_dump(mode="json")
        observations = (data.get("data") or {}).get("observations", {})
        status = "FAIL" if data["status"] != "success" else "PASS" if has_observations(observations) else "EMPTY_BY_DESIGN"
        output[name] = {"status": status, "category": "C" if status == "FAIL" else "A" if status == "PASS" else "B",
                        "ToolResult": data, "raw_backend_refs": capture.references.get(name, [])}
        print(f"{phase} {name}: {status}", flush=True)
    discovery = await capture.provider.prometheus.discover(*capture.provider.window(event), service)
    save(f"{phase}/discovery.json", discovery)
    save(f"{phase}/ToolResults.json", output)
    save(f"{phase}/raw_manifest.json", capture.references)
    return output


async def rca(service):
    capture = Capture("rca")
    event = alert(service, kind="resource")
    workflow = OpsPilotWorkflow(capture.registry(), settings=capture.provider.settings, execution_mode="sequential")
    started = datetime.now(UTC)
    outcome = await workflow.observe(event, trace_id="local-live-rca")
    rationale, llm_used = await workflow.root_cause_agent.explain_existing(
        event, outcome.provisional_candidates, outcome.evidence
    )
    report = build_report(alert=event, outcome=outcome, trace_id="local-live-rca", started_at=started,
                          rationale=rationale, llm_used=llm_used)
    for name, value in {
        "Alert": event.model_dump(mode="json"),
        "ToolResults": [item.model_dump(mode="json") for item in outcome.tool_results],
        "Evidence": [item.model_dump(mode="json") for item in outcome.evidence],
        "TopK": [item.model_dump(mode="json") for item in outcome.provisional_candidates],
        "Gate": [item.model_dump(mode="json") for item in outcome.trace.gate_decisions],
        "ActionHistory": [item.model_dump(mode="json") for item in outcome.trace.action_history],
        "DiagnosisReport": report.model_dump(mode="json"),
        "raw_manifest": capture.references,
        "execution": {"planner": "fallback", "llm_enabled": False, "completed": True,
                      "degraded": report.degraded, "evidence_count": len(outcome.evidence)},
    }.items():
        save(f"rca/{name}.json", value)
    print(f"RCA completed: evidence={len(outcome.evidence)}, degraded={report.degraded}", flush=True)


async def flag(name, enabled):
    # Control plane lives only in this acceptance harness, never in the provider or Alert.
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.get("http://127.0.0.1:14000/api/read")
        response.raise_for_status()
        flags = response.json()
        for config in flags["flags"].values():
            if "off" in config["variants"]:
                config["defaultVariant"] = "off"
                config.pop("targeting", None)
        if enabled:
            flags["flags"][name]["defaultVariant"] = "on"
        response = await client.post("http://127.0.0.1:14000/api/write", json={"data": flags})
        response.raise_for_status()
        for _ in range(20):
            await asyncio.sleep(0.5)
            readback = await client.get("http://127.0.0.1:14000/api/read")
            readback.raise_for_status()
            if readback.json() == flags:
                break
        else:
            raise RuntimeError("Fault configuration readback did not match")
        save(f"control/{name}_{'on' if enabled else 'off'}.json", {"time": datetime.now(UTC).isoformat(), "flags": flags})
        print(f"Fault control: {name} {'on' if enabled else 'off'}", flush=True)


async def traffic():
    capture = Capture("traffic")
    provider = capture.provider
    samples = []
    for iteration in range(2):
        now = datetime.now(UTC)
        discovery = await provider.prometheus.discover(now.timestamp() - 300, now.timestamp(), "frontend")
        candidates = [name for name in discovery["metric_names"]
                      if re.fullmatch(r"traces_span_metrics_calls(?:_total)?", name)]
        counter = await provider.prometheus.api("query", {"query": f"sum({candidates[0]})"}) if candidates else None
        traces = await provider.jaeger.traces("frontend", now.timestamp() - 120, now.timestamp(), limit=20)
        logs = await provider.opensearch.request("POST", "/otel-logs-*/_search", json={
            "size": 1, "track_total_hits": True, "sort": [{"observedTimestamp": "desc"}],
            "_source": ["observedTimestamp", "resource.service.name"],
        })
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get("http://127.0.0.1:18089/stats/requests")
            response.raise_for_status()
            locust = response.json()
            frontend = await client.get("http://127.0.0.1:18080/")
            frontend.raise_for_status()
        sample = {"time": now.isoformat(), "discovery": discovery, "request_counter": counter,
                  "traces": traces, "logs": logs, "load_generator": locust,
                  "frontend_http_status": frontend.status_code}
        samples.append(sample)
        save(f"traffic/sample_{iteration + 1}.json", sample)
        print(f"Traffic sample {iteration + 1} saved", flush=True)
        if iteration == 0:
            await asyncio.sleep(45)
    save("traffic/raw_manifest.json", capture.references)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "rca", "fault-on", "fault-off", "traffic", "fault-smoke"])
    parser.add_argument("--phase", default="normal")
    parser.add_argument("--service", default="frontend")
    parser.add_argument("--flag", default="adHighCpu")
    args = parser.parse_args()
    if args.mode == "fault-smoke":
        try:
            await flag(args.flag, True)
            for elapsed in range(0, 120, 30):
                print(f"Waiting for fault telemetry: {elapsed}/120 seconds", flush=True)
                await asyncio.sleep(30)
            await snapshot("fault_ad", "ad")
            await rca("ad")
        finally:
            await flag(args.flag, False)
        for elapsed in range(0, 90, 30):
            print(f"Waiting for recovery telemetry: {elapsed}/90 seconds", flush=True)
            await asyncio.sleep(30)
        await snapshot("recovery_ad", "ad")
    elif args.mode == "snapshot":
        await snapshot(args.phase, args.service)
    elif args.mode == "rca":
        await rca(args.service)
    elif args.mode == "traffic":
        await traffic()
    else:
        await flag(args.flag, args.mode == "fault-on")


if __name__ == "__main__":
    asyncio.run(main())

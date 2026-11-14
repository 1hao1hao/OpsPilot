"""Read-only V2 RCA probe against configured live observability endpoints."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from opspilot.config import RuntimeSettings
from opspilot.graph.workflow import OpsPilotWorkflow
from opspilot.models import AlertEvent
from opspilot.tools.registry import build_default_registry


async def probe(output: Path, service: str) -> None:
    settings = RuntimeSettings(investigation_mode="v2", observation_backend="otel_demo",
                               investigation_max_rounds=2, investigation_max_tool_calls=6)
    now = datetime.now(UTC)
    alert = AlertEvent(alert_id=f"v2-live-probe-{now.strftime('%Y%m%dT%H%M%SZ')}",
                       service_name=service, alert_type="timeout", severity="P2", timestamp=now,
                       description="Read-only current telemetry probe; no injected fault or ground truth")
    outcome = await OpsPilotWorkflow(build_default_registry(settings=settings), settings=settings).observe(
        alert, trace_id=alert.alert_id)
    summary = {
        "source": "live OpenTelemetry Demo endpoints", "timestamp": now.isoformat(),
        "service": service, "graph_version": settings.graph_version,
        "tools": [{"name": item.tool_name, "target": item.target_service or service,
                   "status": item.status.value, "error_code": item.error_code,
                   "observation_fields": sorted((item.data or {}).get("observations", {}))}
                  for item in outcome.tool_results],
        "evidence_types": [item.evidence_type for item in outcome.evidence],
        "top1": outcome.provisional_candidates[0].root_cause_type.value,
        "verdict": outcome.trace.decision.verdict.value,
        "task_graph_version": outcome.state["task_graph"]["version"],
        "evidence_board_size": len(outcome.state["evidence_board"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="frontend")
    parser.add_argument("--output", type=Path, default=Path("artifacts/v2_demo/live_probe.json"))
    args = parser.parse_args()
    asyncio.run(probe(args.output, args.service))

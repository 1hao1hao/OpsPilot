"""Reproducible synthetic multi-specialist investigation, without answer labels."""

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


class ScenarioProvider:
    supports_scoped_queries = True

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    async def read(self, key: str, alert: AlertEvent) -> dict:
        if alert.service_name != "checkout":
            return {}
        if key == "metric":
            return ({"tp95": {"current": 1500, "baseline": 100}}
                    if self.scenario != "healthy_reference" else
                    {"tp95": {"current": 100, "baseline": 100}})
        if key == "trace":
            if self.scenario == "healthy_reference":
                return {"traces": []}
            return {"traces": [{"trace_id": "two-deps", "spans": [
                {"span_id": "root", "service": "checkout", "status": "OK", "duration_ms": 20,
                 "start_time": 1794614400},
                {"span_id": "db", "parent_span_id": "root", "service": "mysql",
                 "status": "ERROR", "duration_ms": 1400, "start_time": 1794614400},
                {"span_id": "cache", "parent_span_id": "root", "service": "redis",
                 "status": "ERROR", "duration_ms": 1200, "start_time": 1794614400},
            ]}]}
        return {}

    async def read_tool(self, name: str, alert: AlertEvent) -> dict:
        if alert.service_name == "mysql" and name == "db.slowlog":
            return {"slow_query_count": 50 if self.scenario == "two_dependencies" else 0}
        if alert.service_name == "mysql" and name == "db.replication":
            return {"replication_lag_seconds": 21 if self.scenario == "two_dependencies" else 0}
        if alert.service_name == "mysql" and name == "db.connections":
            return {"active_connections": 20, "max_connections": 100}
        if alert.service_name == "redis" and name == "redis.memory":
            return {"memory_usage_percent": 95}
        return {}


async def investigate(scenario: str, *, llm_enabled: bool = False) -> dict:
    alert = AlertEvent(
        alert_id=f"v2-demo-{scenario}", service_name="checkout", alert_type="timeout",
        severity="P1", timestamp=datetime(2026, 11, 14, tzinfo=UTC),
        description="Checkout latency alert; investigate observed dependencies",
    )
    registry = build_default_registry(provider=ScenarioProvider(scenario), timeout_seconds=0.2)
    workflow = OpsPilotWorkflow(registry, settings=RuntimeSettings(
        investigation_mode="v2", investigation_max_rounds=4,
        investigation_max_tool_calls=10, investigation_max_expert_calls=3,
        skills_enabled=scenario != "db_refuted",
        llm_enabled=llm_enabled,
    ))
    events = []
    latest_snapshot = {}

    async def capture(event: str, snapshot: dict) -> None:
        nonlocal latest_snapshot
        latest_snapshot = snapshot
        if event in {"investigation.gate", "investigation.tool.completed"}:
            events.append({
                "event": event, "round": snapshot["round"],
                "tools": snapshot["executed_tool_targets"],
                "tasks": [{"id": task["task_id"], "status": task["status"],
                           "agent": task["assigned_agent"]}
                          for task in snapshot["task_graph"]["tasks"]],
                "evidence_ids": [item["evidence_id"] for item in snapshot["evidence_board"]],
                "verdict": (snapshot.get("decision") or {}).get("verdict"),
            })

    report = await workflow.analyze(alert, trace_id=f"demo-{scenario}", on_state=capture)
    return {"scenario": scenario, "source": "deterministic synthetic ScenarioProvider",
            "report": report.model_dump(mode="json"), "events": events,
            "llm_tokens_used": latest_snapshot.get("llm_tokens_used", 0),
            "agent_results": [{"task_id": item["task_id"], "target_service": item["target_service"],
                               "llm_used": item["llm_used"], "token_usage": item["token_usage"],
                               "local_decisions": item["local_decisions"]}
                              for item in latest_snapshot.get("specialist_results", [])]}


async def main(output: Path, *, llm_enabled: bool = False) -> None:
    cases = ["two_dependencies"] if llm_enabled else ["two_dependencies", "db_refuted", "healthy_reference"]
    results = [await investigate(scenario, llm_enabled=llm_enabled) for scenario in cases]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in results:
        report = item["report"]
        print(f"{item['scenario']}: verdict={report['verdict']}, "
              f"top1={report['primary_root_cause']['root_cause_type']}, "
              f"tools={len(report['investigation']['executed_tools'])}, events={len(item['events'])}")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/v2_demo/demo.json"))
    parser.add_argument("--llm", action="store_true", help="Use the configured external LLM for local agent decisions")
    args = parser.parse_args()
    asyncio.run(main(args.output, llm_enabled=args.llm))

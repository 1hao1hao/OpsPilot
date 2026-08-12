"""Coordinator selects registered read-only tools; it performs no I/O itself."""

from __future__ import annotations

from opspilot.models import AlertEvent, AnalysisPlan, PlanStep
from opspilot.tools import ToolRegistry


class CoordinatorAgent:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(self, alert: AlertEvent) -> AnalysisPlan:
        preferred = [
            "changes.query",
            "metrics.query",
            "logs.query",
            "traces.query",
            "db.inspect",
            "redis.inspect",
            "kafka.inspect",
            "rpc.inspect",
            "topology.query",
        ]
        steps = [
            PlanStep(
                step_id=f"inspect-{name.replace('.', '-')}",
                tool_name=name,
                priority=index,
                reason=f"Collect {name.split('.')[0]} evidence for {alert.alert_type.value} alert",
            )
            for index, name in enumerate(preferred, start=1)
            if name in self.registry.names()
        ]
        return AnalysisPlan(steps=steps)


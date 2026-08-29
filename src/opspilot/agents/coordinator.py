"""Coordinator selects registered read-only tools; it performs no I/O itself."""

from __future__ import annotations

from opspilot.models import AlertEvent, AnalysisPlan, DimensionTask, PlanStep
from opspilot.tools import ToolRegistry


class CoordinatorAgent:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(self, alert: AlertEvent) -> AnalysisPlan:
        dimension_names = {
            "change": "Change correlation",
            "upstream": "Upstream traffic",
            "downstream": "Downstream dependency",
            "cluster": "Cluster resources",
            "errorlog": "Error log patterns",
            "problem": "Related incidents",
        }
        dimension_tools = {
            "change": ["changes.query"],
            "upstream": ["metrics.query", "topology.query", "rpc.inspect"],
            "downstream": ["traces.query", "topology.query", "db.inspect", "redis.inspect", "kafka.inspect", "rpc.inspect"],
            "cluster": ["metrics.query"],
            "errorlog": ["logs.query"],
            "problem": ["alerts.query"],
        }
        expert_domains = {
            "downstream": ["db", "redis", "kafka", "rpc"],
        }
        by_alert_type = {
            "timeout": ["change", "downstream", "cluster", "errorlog", "upstream", "problem"],
            "error_rate": ["change", "downstream", "errorlog", "cluster", "upstream", "problem"],
            "resource": ["cluster", "downstream", "change", "errorlog", "upstream", "problem"],
            "custom": ["change", "upstream", "downstream", "cluster", "errorlog", "problem"],
        }
        selected = by_alert_type[alert.alert_type.value]
        dimensions = [
            DimensionTask(
                dimension=dimension,
                name=dimension_names[dimension],
                priority=index,
                tools=[name for name in dimension_tools[dimension] if name in self.registry.names()],
                expert_domains=expert_domains.get(dimension, []),
                reason=f"Analyze the {dimension} dimension for a {alert.alert_type.value} alert",
            )
            for index, dimension in enumerate(selected, start=1)
        ]
        preferred = list(dict.fromkeys(tool for task in dimensions for tool in task.tools))
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
        return AnalysisPlan(steps=steps, dimensions=dimensions)

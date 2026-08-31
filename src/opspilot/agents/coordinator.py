"""Seed Planner for the adaptive investigation loop."""

from __future__ import annotations

from opspilot.models import AlertEvent, AnalysisPlan, DimensionTask, PlanStep
from opspilot.tools import ToolRegistry

SEED_TOOLS = {
    "timeout": ["metrics.query", "traces.query", "changes.query"],
    "error_rate": ["metrics.query", "logs.query", "traces.query"],
    "resource": ["metrics.query", "logs.query"],
    "custom": ["metrics.query", "logs.query", "changes.query"],
}

TOOL_DIMENSIONS = {
    "metrics.query": "cluster",
    "logs.query": "errorlog",
    "changes.query": "change",
    "traces.query": "downstream",
    "topology.query": "upstream",
    "alerts.query": "problem",
}

DIMENSION_NAMES = {
    "change": "Change correlation",
    "upstream": "Upstream traffic",
    "downstream": "Downstream dependency",
    "cluster": "Cluster resources",
    "errorlog": "Error log patterns",
    "problem": "Related incidents",
}


def dimension_for_tool(tool_name: str, alert_type: str) -> str | None:
    """Map an observation Tool to its L1 semantic dimension."""
    dimension = TOOL_DIMENSIONS.get(tool_name)
    if tool_name == "metrics.query":
        return "upstream" if alert_type in {"timeout", "error_rate"} else "cluster"
    return dimension


class CoordinatorAgent:
    """Create only the low-cost first investigation round.

    Domain Tools and Experts are deliberately absent here. They are selected by
    the Adaptive Planner after the seed observations have been analyzed.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(self, alert: AlertEvent) -> AnalysisPlan:
        selected = list(SEED_TOOLS[alert.alert_type.value])
        label_text = " ".join(f"{key}={value}" for key, value in sorted(alert.labels.items())).lower()

        # Severity and explicit alert metadata may replace the third seed slot,
        # but the seed round remains capped at three low-cost Tools.
        if alert.severity.value == "P0" and "logs.query" not in selected:
            selected.insert(1, "logs.query")
        if any(token in label_text for token in ("deploy", "release", "version", "config")):
            selected.insert(0, "changes.query")
        if any(token in label_text for token in ("dependency", "downstream", "trace", "rpc")):
            selected.insert(0, "traces.query")
        selected = [name for name in dict.fromkeys(selected) if name in self.registry.general_names()][:3]
        # Small test/extension registries may expose only a custom observation
        # Tool. Keep the runtime executable without changing the production
        # default registry's low-cost seed policy.
        if not selected and self.registry.names():
            selected = [self.registry.names()[0]]

        dimensions: list[DimensionTask] = []
        for index, tool_name in enumerate(selected, start=1):
            dimension = dimension_for_tool(tool_name, alert.alert_type.value) or "downstream"
            dimensions.append(
                DimensionTask(
                    dimension=dimension,
                    name=DIMENSION_NAMES[dimension],
                    priority=index,
                    tools=[tool_name],
                    expert_domains=[],
                    reason=(
                        f"Seed {dimension} investigation for alert_type={alert.alert_type.value}, "
                        f"severity={alert.severity.value}"
                    ),
                )
            )

        steps = [
            PlanStep(
                step_id=f"seed-{name.replace('.', '-')}",
                tool_name=name,
                priority=index,
                reason=f"Round 1 low-cost seed observation for {alert.alert_type.value}",
            )
            for index, name in enumerate(selected, start=1)
        ]
        return AnalysisPlan(steps=steps, dimensions=dimensions)

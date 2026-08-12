"""Stage-1 in-process workflow with explicit, recoverable future step boundaries."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from time import perf_counter

from opspilot.agents import CoordinatorAgent, RootCauseAgent
from opspilot.evidence import collect_evidence
from opspilot.models import AlertEvent, DiagnosisReport, RootCauseType, ToolCall, ToolStatus
from opspilot.tools import ToolExecutor, ToolRegistry, build_tool_call_id


class OpsPilotWorkflow:
    def __init__(self, registry: ToolRegistry, root_cause_agent: RootCauseAgent | None = None) -> None:
        self.registry = registry
        self.coordinator = CoordinatorAgent(registry)
        self.root_cause_agent = root_cause_agent or RootCauseAgent()

    async def analyze(self, alert: AlertEvent, *, trace_id: str | None = None) -> DiagnosisReport:
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        started = perf_counter()
        plan = self.coordinator.plan(alert)
        executor = ToolExecutor(self.registry)

        calls = []
        for step in plan.steps:
            definition = self.registry.get(step.tool_name)
            arguments = {"alert": alert.model_dump(mode="json")}
            calls.append(
                ToolCall(
                    tool_call_id=build_tool_call_id(
                        trace_id=trace_id,
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        version=definition.version,
                        arguments=arguments,
                    ),
                    tool_name=step.tool_name,
                    arguments=arguments,
                )
            )
        results = await asyncio.gather(*(executor.execute(call) for call in calls))
        evidence = collect_evidence(alert, results)
        candidates, rationale = self.root_cause_agent.diagnose(alert, evidence)
        failed_sources = sorted(result.tool_name for result in results if result.status == ToolStatus.ERROR)
        primary = candidates[0]
        actions = recommended_actions(primary.root_cause_type)
        finished_at = datetime.now(UTC)
        return DiagnosisReport(
            trace_id=trace_id,
            alert_id=alert.alert_id,
            service_name=alert.service_name,
            candidates=candidates,
            primary_root_cause=primary,
            evidence=evidence,
            tool_executions=executor.executions,
            degraded=bool(failed_sources),
            missing_sources=failed_sources,
            decision_rationale=rationale,
            recommended_actions=actions,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(perf_counter() - started) * 1000,
        )


def recommended_actions(cause: RootCauseType) -> list[str]:
    if cause == RootCauseType.NO_FAULT:
        return ["Continue monitoring and collect more data if the alert persists"]
    if cause.value.startswith("db_"):
        return ["Inspect database capacity and recent query changes", "Mitigate the identified database bottleneck"]
    if cause.value.startswith("redis_"):
        return ["Inspect Redis memory, keys and cache policy"]
    if cause.value.startswith("kafka_"):
        return ["Inspect consumer health and lag by partition"]
    if cause.value.startswith("rpc_"):
        return ["Inspect the affected downstream dependency and timeout policy"]
    if cause == RootCauseType.BAD_DEPLOYMENT:
        return ["Review and, after approval, consider rolling back the recent deployment"]
    return ["Inspect service resource limits and recent workload changes"]

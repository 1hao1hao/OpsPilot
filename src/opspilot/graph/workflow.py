"""Stage-1 in-process workflow with explicit, recoverable future step boundaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from time import perf_counter

from opspilot.agents import CoordinatorAgent, RootCauseAgent, build_runtime_root_cause_agent
from opspilot.config import RuntimeSettings
from opspilot.investigation import AdaptiveInvestigator
from opspilot.models import AlertEvent, DiagnosisReport, RootCauseType, ToolCall, ToolStatus
from opspilot.tools import ToolExecutor, ToolRegistry, build_tool_call_id


class OpsPilotWorkflow:
    def __init__(
        self,
        registry: ToolRegistry,
        root_cause_agent: RootCauseAgent | None = None,
        *,
        execution_mode: str = "parallel",
        settings: RuntimeSettings | None = None,
        promote_expert_evidence: bool = True,
    ) -> None:
        if execution_mode not in {"parallel", "sequential"}:
            raise ValueError(f"unsupported execution mode: {execution_mode}")
        self.registry = registry
        self.settings = settings or RuntimeSettings()
        self.coordinator = CoordinatorAgent(registry)
        self.root_cause_agent = root_cause_agent or build_runtime_root_cause_agent(self.settings)
        self.investigator = AdaptiveInvestigator(
            registry,
            settings=self.settings,
            promote_expert_evidence=promote_expert_evidence,
        )
        self.execution_mode = execution_mode

    async def analyze(self, alert: AlertEvent, *, trace_id: str | None = None) -> DiagnosisReport:
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        started = perf_counter()
        executor = ToolExecutor(self.registry)

        async def execute_tool(tool_name: str, round_number: int, reason: str):
            definition = self.registry.get(tool_name)
            arguments = {"alert": alert.model_dump(mode="json")}
            call = ToolCall(
                tool_call_id=build_tool_call_id(
                    trace_id=trace_id,
                    step_id=f"round-{round_number}:{tool_name}",
                    tool_name=tool_name,
                    version=definition.version,
                    arguments=arguments,
                ),
                tool_name=tool_name,
                arguments=arguments,
            )
            return await executor.execute(call)

        investigation = await self.investigator.run(
            alert,
            execute_tool,
            parallel_seed=self.execution_mode == "parallel",
        )
        results = investigation.tool_results
        dimension_results = investigation.dimension_results
        expert_results = investigation.expert_results
        evidence = list(investigation.evidence)
        algorithm_signals = investigation.algorithm_signals
        matched_rules = investigation.matched_rules
        candidates = investigation.provisional_candidates
        rationale, llm_used = await self.root_cause_agent.explain_existing(alert, candidates, evidence)
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
            dimension_results=dimension_results,
            expert_results=expert_results,
            algorithm_signals=algorithm_signals,
            matched_rules=matched_rules,
            investigation=investigation.trace,
            llm_used=llm_used,
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

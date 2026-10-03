"""Offline wrapper: delegate investigation and reporting to the same engine as Runtime."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from opspilot.agents import RootCauseAgent, build_runtime_root_cause_agent
from opspilot.config import RuntimeSettings
from opspilot.investigation import AdaptiveInvestigator, InvestigationOutcome
from opspilot.investigation.report import build_report
from opspilot.models import AlertEvent, DiagnosisReport, ToolCall
from opspilot.tools import ToolExecutor, ToolRegistry, build_tool_call_id


class OpsPilotWorkflow:
    def __init__(
        self,
        registry: ToolRegistry,
        root_cause_agent: RootCauseAgent | None = None,
        *,
        execution_mode: str = "parallel",
        settings: RuntimeSettings | None = None,
    ) -> None:
        if execution_mode not in {"parallel", "sequential"}:
            raise ValueError(f"unsupported execution mode: {execution_mode}")
        self.registry = registry
        self.settings = settings or RuntimeSettings()
        self.root_cause_agent = root_cause_agent or build_runtime_root_cause_agent(self.settings)
        self.investigator = AdaptiveInvestigator(
            registry,
            settings=self.settings,
        )
        self.execution_mode = execution_mode

    async def analyze(self, alert: AlertEvent, *, trace_id: str | None = None) -> DiagnosisReport:
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        outcome = await self.observe(alert, trace_id=trace_id)
        rationale, llm_used = await self.root_cause_agent.explain_existing(
            alert,
            outcome.provisional_candidates,
            outcome.evidence,
        )
        return build_report(
            alert=alert,
            outcome=outcome,
            trace_id=trace_id,
            started_at=started_at,
            rationale=rationale,
            llm_used=llm_used,
        )

    async def observe(self, alert: AlertEvent, *, trace_id: str) -> InvestigationOutcome:
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

        return await self.investigator.run(alert, execute_tool, parallel_seed=self.execution_mode == "parallel")

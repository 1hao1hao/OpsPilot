"""Checkpointable workflow execution with persistent ToolCall idempotency."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from opspilot.agents import build_runtime_root_cause_agent
from opspilot.config import RuntimeSettings
from opspilot.graph.workflow import recommended_actions
from opspilot.investigation import AdaptiveInvestigator
from opspilot.models import (
    AlertEvent,
    DiagnosisReport,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
    ToolResult,
    ToolStatus,
)
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.errors import CheckpointVersionMismatch
from opspilot.tools import ToolExecutor, ToolRegistry, build_tool_call_id

STEP_EXECUTION_VERSION = "1"


class RecoverableExecution:
    def __init__(
        self,
        repository: RuntimeRepository,
        registry: ToolRegistry,
        settings: RuntimeSettings,
        *,
        before_step: Callable[[str, str], None] | None = None,
        after_checkpoint: Callable[[str, str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.settings = settings
        self.root_cause_agent = build_runtime_root_cause_agent(settings)
        self.investigator = AdaptiveInvestigator(registry, settings=settings)
        self.before_step = before_step
        self.after_checkpoint = after_checkpoint

    async def execute(self, run_id: str) -> DiagnosisReport:
        run = await self.repository.get_run(run_id)
        if run is None:
            raise RuntimeError(f"run not found: {run_id}")
        alert = AlertEvent.model_validate(run.alert_json)
        checkpoint = await self.repository.latest_checkpoint(run_id)
        if checkpoint:
            if (
                checkpoint.schema_version != self.settings.checkpoint_schema_version
                or checkpoint.graph_version != self.settings.graph_version
            ):
                raise CheckpointVersionMismatch(
                    f"checkpoint {checkpoint.checkpoint_id} has schema={checkpoint.schema_version}, "
                    f"graph={checkpoint.graph_version}; expected schema={self.settings.checkpoint_schema_version}, "
                    f"graph={self.settings.graph_version}"
                )
            state = dict(checkpoint.state_json)
        else:
            state = {
                "schema_version": self.settings.checkpoint_schema_version,
                "graph_version": self.settings.graph_version,
                "alert": alert.model_dump(mode="json"),
                "started_at": datetime.now(UTC).isoformat(),
                "tool_results": [],
            }

        async def execute_tool(tool_name: str, round_number: int, reason: str) -> ToolResult:
            step_name = f"tool:{tool_name}"
            await self.repository.begin_step(run_id, step_name, STEP_EXECUTION_VERSION)
            if self.before_step:
                self.before_step(run_id, step_name)
            return await self._execute_tool(
                run_id,
                alert,
                f"round-{round_number}:{tool_name}",
                step_name,
                tool_name,
            )

        async def on_investigation_state(event_type: str, snapshot: dict) -> None:
            state["investigation"] = snapshot
            state["tool_results"] = list(snapshot["tool_results"])
            detail = self._investigation_event_detail(event_type, snapshot)
            await self.repository.append_runtime_event(run_id, event_type, detail=detail)
            if event_type == "investigation.tool.completed":
                latest = snapshot["tool_results"][-1]
                await self._checkpoint(run_id, f"tool:{latest['tool_name']}", latest, state)
                return
            sequence = int(state.get("investigation_event_sequence", 0)) + 1
            state["investigation_event_sequence"] = sequence
            step_name = f"investigation:{sequence}:{event_type}"
            await self._commit(run_id, step_name, detail, state)

        investigation = await self.investigator.run(
            alert,
            execute_tool,
            restored_state=state.get("investigation"),
            on_state=on_investigation_state,
        )
        state["investigation"] = investigation.state
        state["tool_results"] = [item.model_dump(mode="json") for item in investigation.tool_results]
        state["dimension_results"] = [item.model_dump(mode="json") for item in investigation.dimension_results]
        state["expert_results"] = [item.model_dump(mode="json") for item in investigation.expert_results]
        state["base_evidence"] = [item.model_dump(mode="json") for item in investigation.evidence]
        results = investigation.tool_results
        dimension_results = investigation.dimension_results
        expert_results = investigation.expert_results
        evidence = list(investigation.evidence)
        algorithm_signals = investigation.algorithm_signals
        matched_rules = investigation.matched_rules
        candidates = investigation.provisional_candidates
        state["algorithm_signals"] = [item.model_dump(mode="json") for item in algorithm_signals]
        state["matched_rules"] = matched_rules
        state["evidence"] = [item.model_dump(mode="json") for item in evidence]
        state["candidates"] = [item.model_dump(mode="json") for item in candidates]

        if "rationale" not in state:
            rationale, llm_used = await self.root_cause_agent.explain_existing(alert, candidates, evidence)
            state["rationale"] = rationale
            state["llm_used"] = llm_used
            await self._commit(run_id, "explanation", {"candidate_count": len(candidates)}, state)
        else:
            rationale = state["rationale"]
            llm_used = bool(state.get("llm_used", False))

        if "report" in state:
            return DiagnosisReport.model_validate(state["report"])

        results = [ToolResult.model_validate(item) for item in state["tool_results"]]
        failed_sources = sorted(item.tool_name for item in results if item.status == ToolStatus.ERROR)
        finished_at = datetime.now(UTC)
        started_at = datetime.fromisoformat(state["started_at"])
        report = DiagnosisReport(
            trace_id=run_id,
            alert_id=alert.alert_id,
            service_name=alert.service_name,
            candidates=candidates,
            primary_root_cause=candidates[0],
            evidence=evidence,
            tool_executions=[
                ToolExecution(
                    tool_call_id=item.tool_call_id,
                    tool_name=item.tool_name,
                    status=item.status,
                    latency_ms=item.latency_ms,
                    attempt=item.attempt,
                    error_code=item.error_code,
                )
                for item in results
            ],
            dimension_results=dimension_results,
            expert_results=expert_results,
            algorithm_signals=algorithm_signals,
            matched_rules=matched_rules,
            investigation=investigation.trace,
            llm_used=llm_used,
            degraded=bool(failed_sources),
            missing_sources=failed_sources,
            decision_rationale=rationale,
            recommended_actions=recommended_actions(candidates[0].root_cause_type),
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=max((finished_at - started_at).total_seconds() * 1000, 0),
        )
        state["report"] = report.model_dump(mode="json")
        await self._commit(run_id, "report", report.model_dump(mode="json"), state)
        return report

    @staticmethod
    def _investigation_event_detail(event_type: str, snapshot: dict) -> dict:
        detail = {
            "round": snapshot.get("round", 0),
            "executed_tools": list(snapshot.get("executed_tools", [])),
            "invoked_experts": list(snapshot.get("invoked_experts", [])),
        }
        actions = snapshot.get("action_history", [])
        gates = snapshot.get("gate_decisions", [])
        if actions:
            detail["action"] = actions[-1]
        if gates:
            detail["gate"] = gates[-1]
        if event_type == "investigation.tool.completed" and snapshot.get("tool_results"):
            latest = snapshot["tool_results"][-1]
            detail["tool"] = {"name": latest["tool_name"], "status": latest["status"]}
        return detail

    async def _execute_tool(
        self, run_id: str, alert: AlertEvent, step_id: str, step_name: str, tool_name: str
    ) -> ToolResult:
        definition = self.registry.get(tool_name)
        arguments = {"alert": alert.model_dump(mode="json")}
        call = ToolCall(
            tool_call_id=build_tool_call_id(
                trace_id=run_id,
                step_id=step_id,
                tool_name=tool_name,
                version=definition.version,
                arguments=arguments,
            ),
            tool_name=tool_name,
            arguments=arguments,
        )
        existing = await self.repository.get_tool_execution(call.tool_call_id)
        if existing and existing.status == ToolExecutionStatus.SUCCEEDED.value:
            return ToolResult.model_validate(existing.result_json)

        await self.repository.start_tool_execution(
            tool_call_id=call.tool_call_id,
            run_id=run_id,
            step_name=step_name,
            tool_name=tool_name,
        )
        executor = ToolExecutor(self.registry, retry_backoff_seconds=self.settings.retry_backoff_seconds)
        try:
            result = await executor.execute(call)
        except ValidationError as exc:
            result = ToolResult(
                tool_call_id=call.tool_call_id,
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                error_code="invalid_tool_schema",
                error_message=str(exc),
                latency_ms=0,
            )
        await self.repository.finish_tool_execution(call.tool_call_id, result.model_dump(mode="json"))
        return result

    async def _commit(self, run_id: str, step_name: str, output: dict, state: dict) -> None:
        await self.repository.begin_step(run_id, step_name, STEP_EXECUTION_VERSION)
        if self.before_step:
            self.before_step(run_id, step_name)
        await self._checkpoint(run_id, step_name, output, state)

    async def _checkpoint(self, run_id: str, step_name: str, output: dict, state: dict) -> None:
        await self.repository.complete_step_with_checkpoint(
            run_id=run_id,
            step_name=step_name,
            execution_version=STEP_EXECUTION_VERSION,
            output=output,
            state=dict(state),
            schema_version=self.settings.checkpoint_schema_version,
            graph_version=self.settings.graph_version,
        )
        if self.after_checkpoint:
            self.after_checkpoint(run_id, step_name)

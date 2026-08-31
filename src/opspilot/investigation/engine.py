"""Execute and checkpoint a bounded adaptive investigation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from opspilot.agents import CoordinatorAgent, RootCauseAgent
from opspilot.config import RuntimeSettings
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.planner import ActionValidator, EvidenceGate, LLMAdaptivePlanner
from opspilot.models import (
    AlertEvent,
    AlgorithmSignal,
    AnalysisPlan,
    DimensionTask,
    Evidence,
    EvidenceGateDecision,
    InvestigationAction,
    InvestigationActionType,
    InvestigationTrace,
    RootCauseCandidate,
    SemanticAnalysisResult,
    ToolResult,
)
from opspilot.tools import ToolRegistry

ToolRunner = Callable[[str, int, str], Awaitable[ToolResult]]
StateCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class InvestigationOutcome:
    tool_results: list[ToolResult]
    dimension_results: list[SemanticAnalysisResult]
    expert_results: list[SemanticAnalysisResult]
    evidence: list[Evidence]
    provisional_candidates: list[RootCauseCandidate]
    algorithm_signals: list[AlgorithmSignal]
    matched_rules: list[str]
    trace: InvestigationTrace
    state: dict[str, Any]


class AdaptiveInvestigator:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        settings: RuntimeSettings | None = None,
        ranker: RootCauseAgent | None = None,
        promote_expert_evidence: bool = True,
        planner: LLMAdaptivePlanner | None = None,
        analysis_engine: DeterministicEvidenceEngine | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings or RuntimeSettings()
        self.seed_planner = CoordinatorAgent(registry)
        self.ranker = ranker or RootCauseAgent()
        self.planner = planner or LLMAdaptivePlanner.from_settings(registry, self.settings)
        self.validator = ActionValidator(registry)
        self.analysis_engine = analysis_engine or DeterministicEvidenceEngine(
            ranker=self.ranker,
            promote_expert_evidence=promote_expert_evidence,
        )
        self.gate = EvidenceGate(
            confidence=self.settings.evidence_gate_confidence,
            margin=self.settings.evidence_gate_margin,
            min_sources=self.settings.evidence_gate_min_sources,
        )

    async def run(
        self,
        alert: AlertEvent,
        execute_tool: ToolRunner,
        *,
        restored_state: dict[str, Any] | None = None,
        on_state: StateCallback | None = None,
        parallel_seed: bool = False,
    ) -> InvestigationOutcome:
        state = self._restore(restored_state)
        seed_plan = self.seed_planner.plan(alert)
        for step in seed_plan.steps:
            self.validator.validate_seed_tool(step.tool_name)

        state["round"] = max(state["round"], 1)
        pending_seed = [step for step in seed_plan.steps if step.tool_name not in state["executed_tools"]]
        remaining = self.settings.investigation_max_tool_calls - len(state["executed_tools"])
        pending_seed = pending_seed[:remaining]
        if parallel_seed and on_state is None:
            await asyncio.gather(*(
                self._inspect(
                    state,
                    alert,
                    step.tool_name,
                    step.reason,
                    execute_tool,
                    on_state,
                    round_number=1,
                )
                for step in pending_seed
            ))
        else:
            for step in pending_seed:
                await self._inspect(
                    state,
                    alert,
                    step.tool_name,
                    step.reason,
                    execute_tool,
                    on_state,
                    round_number=1,
                )

        pending = next(
            (item for item in reversed(state["action_history"]) if item.status == "planned" and item.round > 1),
            None,
        )
        if pending is not None:
            state["round"] = max(state["round"], pending.round)
            await self._execute_action(state, alert, pending, execute_tool, on_state)

        analysis_plan = self._analysis_plan(seed_plan, state["executed_tools"])
        self._analyze(state, alert, analysis_plan)
        await self._notify(on_state, "investigation.round.completed", state)

        while True:
            exhausted = self._budget_exhausted(state)
            gate = self.gate.evaluate(
                state["provisional_candidates"],
                state["evidence"],
                budget_exhausted=exhausted,
            )
            state["gate_decisions"].append(gate)
            await self._notify(on_state, "investigation.gate", state)
            if gate.sufficient or exhausted:
                state["stop_reason"] = gate.reason
                break

            next_round = state["round"] + 1
            action = await self.planner.decide(
                alert=alert,
                round_number=next_round,
                dimension_results=state["dimension_results"],
                expert_results=state["expert_results"],
                evidence=state["evidence"],
                candidates=state["provisional_candidates"],
                executed_tools=state["executed_tools"],
                invoked_experts=state["invoked_experts"],
                action_history=state["action_history"],
                action_identities={item.identity for item in state["action_history"]},
                remaining_round_budget=self.settings.investigation_max_rounds - state["round"],
                remaining_tool_budget=self.settings.investigation_max_tool_calls - len(state["executed_tools"]),
                remaining_expert_budget=self.settings.investigation_max_expert_calls - len(state["invoked_experts"]),
            )
            if action is None:
                state["stop_reason"] = "no legal non-duplicate investigation action"
                break
            if action.identity in {item.identity for item in state["action_history"]}:
                state["duplicate_actions"] += 1
                state["stop_reason"] = "duplicate action rejected"
                break
            state["round"] = next_round
            state["action_history"].append(action)
            await self._notify(on_state, "investigation.action.planned", state)

            await self._execute_action(state, alert, action, execute_tool, on_state)

            analysis_plan = self._analysis_plan(seed_plan, state["executed_tools"])
            self._analyze(state, alert, analysis_plan)
            await self._notify(on_state, "investigation.round.completed", state)

        trace = InvestigationTrace(
            rounds=max(state["round"], 1),
            action_history=state["action_history"],
            gate_decisions=state["gate_decisions"],
            executed_tools=state["executed_tools"],
            invoked_experts=state["invoked_experts"],
            stop_reason=state["stop_reason"],
            tool_budget_used=len(state["executed_tools"]),
            expert_budget_used=len(state["invoked_experts"]),
            duplicate_actions=state["duplicate_actions"],
        )
        snapshot = self._snapshot(state)
        return InvestigationOutcome(
            tool_results=state["tool_results"],
            dimension_results=state["dimension_results"],
            expert_results=state["expert_results"],
            evidence=state["evidence"],
            provisional_candidates=state["provisional_candidates"],
            algorithm_signals=state["algorithm_signals"],
            matched_rules=state["matched_rules"],
            trace=trace,
            state=snapshot,
        )

    async def _execute_action(
        self,
        state: dict[str, Any],
        alert: AlertEvent,
        action: InvestigationAction,
        execute_tool: ToolRunner,
        on_state: StateCallback | None,
    ) -> None:
        self.validator.validate_planner_action(action)
        if action.action_type == InvestigationActionType.INSPECT_TOOL:
            if action.target not in state["executed_tools"]:
                await self._inspect(
                    state,
                    alert,
                    action.target,
                    action.reason,
                    execute_tool,
                    on_state,
                    round_number=action.round,
                    record_action=False,
                )
            action.status = "succeeded"
            return
        if action.action_type == InvestigationActionType.INVOKE_EXPERT:
            if action.target not in state["invoked_experts"]:
                state["invoked_experts"].append(action.target)
            tools = self.planner.expert_tools(
                action.target,
                alert,
                state["dimension_results"],
                state["evidence"],
            )
            for tool_name in tools:
                self.validator.validate_expert_tool(action.target, tool_name)
                if len(state["executed_tools"]) >= self.settings.investigation_max_tool_calls:
                    break
                if tool_name in state["executed_tools"]:
                    continue
                await self._inspect(
                    state,
                    alert,
                    tool_name,
                    f"{action.target} Expert drill-down: {action.reason}",
                    execute_tool,
                    on_state,
                    round_number=action.round,
                )
            action.status = "succeeded"
            await self._notify(on_state, "investigation.expert.completed", state)

    async def _inspect(
        self,
        state: dict[str, Any],
        alert: AlertEvent,
        tool_name: str,
        reason: str,
        execute_tool: ToolRunner,
        on_state: StateCallback | None,
        *,
        round_number: int,
        record_action: bool = True,
    ) -> None:
        if tool_name in state["executed_tools"]:
            state["duplicate_actions"] += 1
            return
        action = InvestigationAction(
            action_type=InvestigationActionType.INSPECT_TOOL,
            target=tool_name,
            reason=reason,
            round=round_number,
            arguments={"service_name": alert.service_name},
        )
        if record_action:
            state["action_history"].append(action)
        result = await execute_tool(tool_name, round_number, reason)
        state["tool_results"].append(result)
        state["executed_tools"].append(tool_name)
        action.status = "succeeded" if result.status.value == "success" else "failed"
        await self._notify(on_state, "investigation.tool.completed", state)

    def _analyze(self, state: dict[str, Any], alert: AlertEvent, plan: AnalysisPlan) -> None:
        analysis = self.analysis_engine.analyze(
            alert,
            plan,
            state["tool_results"],
            state["invoked_experts"],
        )
        state["dimension_results"] = analysis.dimension_results
        state["expert_results"] = analysis.expert_results
        state["algorithm_signals"] = analysis.algorithm_signals
        state["matched_rules"] = analysis.matched_rules
        state["evidence"] = analysis.evidence
        state["provisional_candidates"] = analysis.candidates

    @staticmethod
    def _analysis_plan(seed: AnalysisPlan, executed_tools: list[str]) -> AnalysisPlan:
        dimensions = list(seed.dimensions)
        mapping = {
            "topology.query": "upstream",
            "alerts.query": "problem",
        }
        existing = {item.dimension for item in dimensions}
        for tool_name in executed_tools:
            dimension = mapping.get(tool_name)
            if dimension and dimension not in existing:
                dimensions.append(
                    DimensionTask(
                        dimension=dimension,
                        name=dimension.title(),
                        priority=len(dimensions) + 1,
                        tools=[tool_name],
                        reason=f"Adaptive inspection selected {tool_name}",
                    )
                )
                existing.add(dimension)
        return AnalysisPlan(steps=seed.steps, dimensions=dimensions)

    def _budget_exhausted(self, state: dict[str, Any]) -> bool:
        return (
            state["round"] >= self.settings.investigation_max_rounds
            or len(state["executed_tools"]) >= self.settings.investigation_max_tool_calls
            or len(state["invoked_experts"]) >= self.settings.investigation_max_expert_calls
        )

    @staticmethod
    async def _notify(callback: StateCallback | None, event: str, state: dict[str, Any]) -> None:
        if callback:
            await callback(event, AdaptiveInvestigator._snapshot(state))

    @staticmethod
    def _restore(restored: dict[str, Any] | None) -> dict[str, Any]:
        data = restored or {}
        return {
            "round": int(data.get("round", 0)),
            "action_history": [InvestigationAction.model_validate(item) for item in data.get("action_history", [])],
            "tool_results": [ToolResult.model_validate(item) for item in data.get("tool_results", [])],
            "executed_tools": list(data.get("executed_tools", [])),
            "invoked_experts": list(data.get("invoked_experts", [])),
            "dimension_results": [SemanticAnalysisResult.model_validate(item) for item in data.get("dimension_results", [])],
            "expert_results": [SemanticAnalysisResult.model_validate(item) for item in data.get("expert_results", [])],
            "algorithm_signals": [AlgorithmSignal.model_validate(item) for item in data.get("algorithm_signals", [])],
            "matched_rules": list(data.get("matched_rules", [])),
            "evidence": [Evidence.model_validate(item) for item in data.get("evidence", [])],
            "provisional_candidates": [RootCauseCandidate.model_validate(item) for item in data.get("provisional_candidates", [])],
            "gate_decisions": [
                EvidenceGateDecision.model_validate(item) for item in data.get("gate_decisions", [])
            ],
            "duplicate_actions": int(data.get("duplicate_actions", 0)),
            "stop_reason": str(data.get("stop_reason", "")),
        }

    @staticmethod
    def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "round": state["round"],
            "action_history": [item.model_dump(mode="json") for item in state["action_history"]],
            "tool_results": [item.model_dump(mode="json") for item in state["tool_results"]],
            "executed_tools": list(state["executed_tools"]),
            "invoked_experts": list(state["invoked_experts"]),
            "dimension_results": [item.model_dump(mode="json") for item in state["dimension_results"]],
            "expert_results": [item.model_dump(mode="json") for item in state["expert_results"]],
            "algorithm_signals": [item.model_dump(mode="json") for item in state["algorithm_signals"]],
            "matched_rules": list(state["matched_rules"]),
            "evidence": [item.model_dump(mode="json") for item in state["evidence"]],
            "provisional_candidates": [item.model_dump(mode="json") for item in state["provisional_candidates"]],
            "gate_decisions": [item.model_dump(mode="json") for item in state["gate_decisions"]],
            "duplicate_actions": state["duplicate_actions"],
            "stop_reason": state["stop_reason"],
        }

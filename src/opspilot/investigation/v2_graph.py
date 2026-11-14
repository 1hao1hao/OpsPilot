"""LangGraph coordinator for evidence-driven specialist dispatch."""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field

from opspilot.agents import CoordinatorAgent
from opspilot.config import RuntimeSettings
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.decision import decide_diagnosis, prepare_decision
from opspilot.investigation.engine import InvestigationOutcome, ToolRunner
from opspilot.investigation.planner import EvidenceGate
from opspilot.investigation.specialist import SpecialistAgent, SpecialistContext, SpecialistResult
from opspilot.investigation.tasks import InvestigationPlan, InvestigationTask, TaskStatus, reconcile_plan
from opspilot.models import (
    AlertEvent,
    DiagnosisDecision,
    DiagnosisVerdict,
    Evidence,
    EvidenceGateDecision,
    EvidenceRequirement,
    InvestigationAction,
    InvestigationActionType,
    InvestigationTrace,
    ObservationQuality,
    RootCauseCandidate,
    RootCauseType,
    ToolResult,
)
from opspilot.skills import SkillCatalog
from opspilot.tools import ToolRegistry


class EvidenceLineage(BaseModel):
    """Task and observation provenance without changing the V1 Evidence API."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    tool_call_id: str
    task_id: str | None = None
    agent_name: str | None = None
    service: str
    observed_at: datetime
    source_group: str
    supports: list[RootCauseType] = Field(default_factory=list)
    contradicts: list[RootCauseType] = Field(default_factory=list)
    observation_qualities: list[ObservationQuality] = Field(default_factory=list)
    tool_result_schema_version: str


class GraphState(TypedDict, total=False):
    alert: AlertEvent
    tool_results: list[ToolResult]
    evidence: list[Evidence]
    evidence_board: list[EvidenceLineage]
    candidates: list[RootCauseCandidate]
    requirements: list[EvidenceRequirement]
    requirements_version: int
    plan: InvestigationPlan
    decision: DiagnosisDecision
    decision_history: list[DiagnosisDecision]
    gates: list[EvidenceGateDecision]
    round: int
    executed_targets: list[str]
    actions: list[InvestigationAction]
    specialist_results: Annotated[list[SpecialistResult], operator.add]
    merged_results: int
    # Send payload is task-local; the coordinator never shares a mutable board.
    task: InvestigationTask
    task_budget: int


class GraphInvestigator:
    """A graph whose dispatch, merge and replan are real execution nodes."""

    def __init__(self, registry: ToolRegistry, *, settings: RuntimeSettings,
                 skills: SkillCatalog | None = None) -> None:
        self.registry = registry
        self.settings = settings
        self.skills = skills
        self.coordinator = CoordinatorAgent(registry)
        self.analysis = DeterministicEvidenceEngine()
        self.specialist = SpecialistAgent(registry)
        self.gate = EvidenceGate(confidence=settings.evidence_gate_confidence,
                                 margin=settings.evidence_gate_margin,
                                 min_sources=settings.evidence_gate_min_sources)
        self._runner: ContextVar[ToolRunner | None] = ContextVar("v2_tool_runner", default=None)
        self._callback: ContextVar[Callable[[str, dict[str, Any]], Awaitable[None]] | None] = ContextVar(
            "v2_state_callback", default=None)
        builder = StateGraph(GraphState)
        builder.add_node("seed", self._seed)
        builder.add_node("analyze", self._analyze)
        builder.add_node("specialist", self._specialist)
        builder.add_node("merge", self._merge)
        builder.add_edge(START, "seed")
        builder.add_edge("seed", "analyze")
        builder.add_conditional_edges("analyze", self._dispatch)
        builder.add_edge("specialist", "merge")
        builder.add_edge("merge", "analyze")
        self.graph = builder.compile()

    async def run(
        self, alert: AlertEvent, execute_tool: ToolRunner, *,
        restored_state: dict[str, Any] | None = None,
        on_state: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> InvestigationOutcome:
        token = self._runner.set(execute_tool)
        callback_token = self._callback.set(on_state)
        initial: GraphState = {
            "alert": alert, "tool_results": [], "evidence": [], "evidence_board": [], "candidates": [],
            "requirements": [], "requirements_version": 0, "plan": InvestigationPlan(version=0),
            "decision_history": [], "gates": [], "round": 1, "executed_targets": [],
            "actions": [], "specialist_results": [], "merged_results": 0,
        }
        if restored_state:
            initial.update(self._restore(restored_state))
        try:
            state = await self.graph.ainvoke(
                initial, config={"recursion_limit": 4 * self.settings.investigation_max_rounds + 8})
        finally:
            self._runner.reset(token)
            self._callback.reset(callback_token)
        decision = state["decision"]
        stop_reason = decision.reason if decision.verdict != DiagnosisVerdict.INCONCLUSIVE else "budget or legal actions exhausted"
        trace = InvestigationTrace(
            rounds=state["round"], action_history=state["actions"], gate_decisions=state["gates"],
            executed_tools=[item.tool_name for item in state["tool_results"]],
            executed_tool_targets=state["executed_targets"], invoked_experts=[
                item.domain for item in state["plan"].tasks if item.attempts > 0 and item.domain != "general"],
            stop_reason=stop_reason, tool_budget_used=len(state["tool_results"]),
            expert_budget_used=sum(item.attempts > 0 and item.domain != "general" for item in state["plan"].tasks),
            decision=decision, decision_history=state["decision_history"],
            requirements_version=state["requirements_version"],
        )
        snapshot = self._snapshot(state)
        return InvestigationOutcome(state["tool_results"], state["evidence"], state["candidates"], trace, snapshot)

    @staticmethod
    def _restore(data: dict[str, Any]) -> GraphState:
        return {
            "tool_results": [ToolResult.model_validate(item) for item in data.get("tool_results", [])],
            "evidence": [Evidence.model_validate(item) for item in data.get("evidence", [])],
            "evidence_board": [EvidenceLineage.model_validate(item) for item in data.get("evidence_board", [])],
            "candidates": [RootCauseCandidate.model_validate(item) for item in data.get("provisional_candidates", [])],
            "requirements": [EvidenceRequirement.model_validate(item) for item in data.get("requirements", [])],
            "requirements_version": int(data.get("requirements_version", 0)),
            "plan": InvestigationPlan.model_validate(data.get("task_graph", {"version": 0})),
            "decision_history": [DiagnosisDecision.model_validate(item) for item in data.get("decision_history", [])],
            "gates": [EvidenceGateDecision.model_validate(item) for item in data.get("gate_decisions", [])],
            "round": max(1, int(data.get("round", 1))),
            "executed_targets": list(data.get("executed_tool_targets", [])),
            "actions": [InvestigationAction.model_validate(item) for item in data.get("action_history", [])],
            "specialist_results": [SpecialistResult.model_validate(item) for item in data.get("specialist_results", [])],
            "merged_results": int(data.get("merged_results", len(data.get("specialist_results", [])))),
        }

    @staticmethod
    def _snapshot(state: GraphState) -> dict[str, Any]:
        return {
            "round": state["round"], "tool_results": [item.model_dump(mode="json") for item in state["tool_results"]],
            "executed_tools": [item.tool_name for item in state["tool_results"]],
            "executed_tool_targets": state["executed_targets"],
            "action_history": [item.model_dump(mode="json") for item in state["actions"]],
            "invoked_experts": [item.domain for item in state["plan"].tasks if item.attempts > 0 and item.domain != "general"],
            "evidence": [item.model_dump(mode="json") for item in state["evidence"]],
            "evidence_board": [item.model_dump(mode="json") for item in state["evidence_board"]],
            "provisional_candidates": [item.model_dump(mode="json") for item in state["candidates"]],
            "requirements": [item.model_dump(mode="json") for item in state["requirements"]],
            "requirements_version": state["requirements_version"],
            "task_graph": state["plan"].model_dump(mode="json"),
            "specialist_results": [item.model_dump(mode="json") for item in state["specialist_results"]],
            "merged_results": state["merged_results"],
            "gate_decisions": [item.model_dump(mode="json") for item in state["gates"]],
            "decision": state["decision"].model_dump(mode="json") if state.get("decision") else None,
            "decision_history": [item.model_dump(mode="json") for item in state["decision_history"]],
        }

    async def _emit(self, event: str, state: GraphState) -> None:
        callback = self._callback.get()
        if callback is not None:
            await callback(event, self._snapshot(state))

    async def _seed(self, state: GraphState) -> GraphState:
        runner = self._runner.get()
        assert runner is not None
        results = list(state["tool_results"])
        actions = list(state["actions"])
        executed = list(state["executed_targets"])
        for step in self.coordinator.plan(state["alert"]).steps[:self.settings.investigation_max_tool_calls]:
            identity = f"{step.tool_name}|{state['alert'].service_name}"
            if identity in executed:
                continue
            result = await runner(step.tool_name, 1, step.reason, state["alert"].service_name)
            results.append(result)
            executed.append(identity)
            actions.append(InvestigationAction(action_type=InvestigationActionType.INSPECT_TOOL,
                                               target=step.tool_name, reason=step.reason, round=1,
                                               arguments={"service_name": state["alert"].service_name},
                                               status="succeeded" if result.status.value == "success" else "failed"))
            await self._emit("investigation.tool.completed", {
                **state, "tool_results": results, "executed_targets": executed, "actions": actions})
        return {"tool_results": results, "executed_targets": executed, "actions": actions}

    async def _analyze(self, state: GraphState) -> GraphState:
        analysis = self.analysis.analyze(state["alert"], state["tool_results"])
        prepared = prepare_decision(
            state["alert"], state["tool_results"], analysis.evidence, analysis.candidates,
            previous_requirements=state["requirements"], previous_version=state["requirements_version"],
        )
        exhausted = (state["round"] >= self.settings.investigation_max_rounds or
                     len(state["tool_results"]) >= self.settings.investigation_max_tool_calls)
        gate = self.gate.evaluate(analysis.candidates, analysis.evidence, budget_exhausted=exhausted,
                                  requirements=prepared.requirements,
                                  requirements_version=prepared.requirements_version)
        decision = decide_diagnosis(state["alert"], state["tool_results"], analysis.evidence,
                                    analysis.candidates, gate, prepared=prepared)
        plan = reconcile_plan(prepared.requirements, analysis.candidates, state["plan"], skills=self.skills)
        board = self._lineage(state, analysis.evidence, prepared.observations)
        update = {"evidence": analysis.evidence, "candidates": analysis.candidates,
                "evidence_board": board,
                "requirements": prepared.requirements, "requirements_version": prepared.requirements_version,
                "gates": [*state["gates"], gate], "decision": decision,
                "decision_history": [*state["decision_history"], decision], "plan": plan}
        await self._emit("investigation.gate", {**state, **update})
        return update

    @staticmethod
    def _lineage(state: GraphState, evidence: list[Evidence], observations) -> list[EvidenceLineage]:
        by_call = {item.tool_call_id: item for item in state["tool_results"]}
        by_source = {f"tool:{item.tool_name}:{item.tool_call_id}": item for item in state["tool_results"]}
        by_task = {item.tool_call_id: (result.task_id, next((task.assigned_agent for task in state["plan"].tasks
                                                             if task.task_id == result.task_id), None))
                   for result in state["specialist_results"] for item in result.tool_results}
        board = []
        for item in evidence:
            result = by_source.get(item.source_group)
            if result is None and item.raw_ref:
                result = by_call.get(item.raw_ref)
            if result is None:
                continue
            task_id, agent = by_task.get(result.tool_call_id, (None, "coordinator"))
            qualities = sorted({row.quality for row in observations if row.observation_id.startswith(
                result.tool_call_id + ":")}, key=lambda quality: quality.value)
            board.append(EvidenceLineage(
                evidence_id=item.evidence_id, tool_call_id=result.tool_call_id,
                task_id=task_id, agent_name=agent, service=item.service,
                observed_at=item.observed_at, source_group=item.source_group,
                supports=item.supports, contradicts=item.contradicts,
                observation_qualities=qualities, tool_result_schema_version=result.schema_version,
            ))
        return sorted(board, key=lambda row: (row.evidence_id, row.tool_call_id))

    def _dispatch(self, state: GraphState):
        if (state["decision"].verdict != DiagnosisVerdict.INCONCLUSIVE or
                state["round"] >= self.settings.investigation_max_rounds):
            return END
        remaining = self.settings.investigation_max_tool_calls - len(state["tool_results"])
        if remaining <= 0:
            return END
        sends = []
        used = set(state["executed_targets"])
        expert_remaining = self.settings.investigation_max_expert_calls - sum(
            task.attempts for task in state["plan"].tasks if task.domain != "general")
        for task in state["plan"].tasks:
            if (task.status != TaskStatus.READY or remaining <= 0 or
                    len(sends) >= self.settings.investigation_max_parallel_tasks):
                continue
            if task.domain != "general" and expert_remaining <= 0:
                continue
            if any(dep not in {item.task_id for item in state["plan"].tasks if item.status == TaskStatus.SATISFIED}
                   for dep in task.dependencies):
                continue
            allowed = [name for name in task.allowed_tools if f"{name}|{task.target_service}" not in used]
            if not allowed:
                continue
            budget = min(2, len(allowed), remaining)
            sends.append(Send("specialist", {"alert": state["alert"],
                                             "task": task.model_copy(update={"allowed_tools": allowed}),
                                             "round": state["round"] + 1, "task_budget": budget}))
            used.update(f"{name}|{task.target_service}" for name in allowed[:budget])
            remaining -= budget
            if task.domain != "general":
                expert_remaining -= 1
        return sends or END

    async def _specialist(self, state: GraphState) -> GraphState:
        runner = self._runner.get()
        assert runner is not None
        context = SpecialistContext(alert=state["alert"], task=state["task"],
                                    round_number=state["round"])
        result = await self.specialist.investigate(context, runner,
                                                    max_tool_calls=state["task_budget"])
        return {"specialist_results": [result]}

    async def _merge(self, state: GraphState) -> GraphState:
        # Map completion order varies with latency; merge by stable task ID so
        # checkpoints, budgets and later planning see a reproducible order.
        new = sorted(state["specialist_results"][state["merged_results"]:],
                     key=lambda item: item.task_id)
        results = list(state["tool_results"])
        executed = list(state["executed_targets"])
        actions = list(state["actions"])
        plan = state["plan"].model_copy(deep=True)
        by_id = {task.task_id: task for task in plan.tasks}
        merged_count = state["merged_results"]
        for task_result in new:
            task = by_id[task_result.task_id]
            task.attempts += 1
            task.status = (TaskStatus.BLOCKED if task_result.unresolved_questions and
                           not task_result.suggested_tools else TaskStatus.READY)
            task.result_observation_ids.extend(item.tool_call_id for item in task_result.tool_results)
            task.stop_reason = task_result.stop_reason
            for item in task_result.tool_results:
                results.append(item)
                executed.append(f"{item.tool_name}|{task_result.target_service}")
                actions.append(InvestigationAction(action_type=InvestigationActionType.INSPECT_TOOL,
                                                   target=item.tool_name, reason=task_result.stop_reason,
                                                   round=state["round"] + 1,
                                                   arguments={"service_name": task_result.target_service},
                                                   status="succeeded" if item.status.value == "success" else "failed"))
                await self._emit("investigation.tool.completed", {
                    **state, "tool_results": results, "executed_targets": executed, "actions": actions,
                    "plan": plan, "specialist_results": state["specialist_results"][:merged_count],
                    "merged_results": merged_count})
            merged_count += 1
        return {"tool_results": results, "executed_targets": executed,
                "actions": actions, "plan": plan,
                "merged_results": len(state["specialist_results"]), "round": state["round"] + 1}

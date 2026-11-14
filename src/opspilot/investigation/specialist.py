"""Bounded, independent investigation of one evidence requirement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.tasks import InvestigationTask, TaskStatus
from opspilot.models import AlertEvent, Evidence, RootCauseType, ToolResult, ToolStatus
from opspilot.tools import ToolRegistry

SpecialistToolRunner = Callable[[str, int, str, str | None], Awaitable[ToolResult]]


class SpecialistContext(BaseModel):
    """A task-local view; other agents' mutable state is not shared."""

    model_config = ConfigDict(extra="forbid")
    alert: AlertEvent
    task: InvestigationTask
    prior_evidence: list[Evidence] = Field(default_factory=list)
    round_number: int = Field(ge=1)


class SpecialistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    target_service: str
    tool_results: list[ToolResult] = Field(default_factory=list)
    supporting_evidence: list[Evidence] = Field(default_factory=list)
    contradicting_evidence: list[Evidence] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    tool_calls: int = Field(ge=0)
    stop_reason: str


class SpecialistAgent:
    """Use scoped tools in local priority order and reassess after each read.

    This deterministic fallback is usable when an LLM is unavailable. The
    supervisor owns task assignment, budgets and final diagnosis.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.analysis = DeterministicEvidenceEngine()

    async def investigate(
        self, context: SpecialistContext, execute_tool: SpecialistToolRunner,
        *, max_tool_calls: int = 2,
    ) -> SpecialistResult:
        task = context.task
        if task.status != TaskStatus.READY:
            raise ValueError("specialist can only execute a READY task")
        if max_tool_calls < 1:
            raise ValueError("specialist requires a positive tool budget")
        allowed = []
        for name in task.allowed_tools:
            definition = self.registry.get(name)
            if definition.side_effect:
                raise ValueError(f"specialist tool is not read-only: {name}")
            if task.domain != "general" and not name.startswith(task.domain + "."):
                raise ValueError(f"tool outside specialist domain: {name}")
            if name not in allowed:
                allowed.append(name)
        if not allowed:
            return SpecialistResult(task_id=task.task_id, target_service=task.target_service,
                                    unresolved_questions=[task.objective], tool_calls=0,
                                    stop_reason="no authorized measurement")

        observed: list[ToolResult] = []
        supporting: list[Evidence] = []
        contradicting: list[Evidence] = []
        candidate = next((cause for cause in RootCauseType if cause.value == task.candidate_cause), None)
        stop_reason = "tool budget exhausted"
        for name in allowed[:max_tool_calls]:
            result = await execute_tool(name, context.round_number, task.objective, task.target_service)
            if result.target_service is None:
                result = result.model_copy(update={"target_service": task.target_service})
            observed.append(result)
            if result.status != ToolStatus.SUCCESS:
                stop_reason = "measurement failed; trying another authorized tool"
                continue
            analysis = self.analysis.analyze(context.alert, [result])
            relevant = [item for item in analysis.evidence if item.service == task.target_service]
            if candidate is not None:
                supporting = [item for item in relevant if candidate in item.supports]
                contradicting = [item for item in relevant if candidate in item.contradicts]
            else:
                supporting = relevant
            if supporting or contradicting:
                stop_reason = "scoped evidence collected"
                break
            stop_reason = "measurement yielded no relevant evidence"

        remaining = [name for name in allowed if name not in {item.tool_name for item in observed}]
        unresolved = [] if supporting or contradicting else [task.objective]
        return SpecialistResult(
            task_id=task.task_id, target_service=task.target_service,
            tool_results=observed, supporting_evidence=supporting,
            contradicting_evidence=contradicting, unresolved_questions=unresolved,
            suggested_tools=remaining if unresolved else [], tool_calls=len(observed), stop_reason=stop_reason,
        )

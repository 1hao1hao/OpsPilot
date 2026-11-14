"""Versioned investigation tasks derived from current evidence needs."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from opspilot.models import EvidenceRequirement, RootCauseCandidate
from opspilot.skills import SkillCatalog


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SATISFIED = "SATISFIED"
    CANCELLED = "CANCELLED"


class TaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    status: TaskStatus
    reason: str


class InvestigationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    objective: str
    target_service: str
    domain: str
    candidate_cause: str | None = None
    required_signal: str
    dependencies: list[str] = Field(default_factory=list)
    priority: int = Field(ge=0)
    estimated_cost: int = Field(default=1, ge=1)
    assigned_agent: str
    selected_skill: str | None = None
    skill_version: str | None = None
    skill_loaded_bytes: int = 0
    skill_reason: str | None = None
    skill_tool_order: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    history: list[TaskEvent] = Field(default_factory=list)
    attempts: int = Field(default=0, ge=0)
    result_observation_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = None


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    tasks: list[InvestigationTask] = Field(default_factory=list)


TOOL_DOMAINS = {
    "db.": "db", "redis.": "redis", "kafka.": "kafka", "rpc.": "rpc",
}


def reconcile_plan(
    requirements: list[EvidenceRequirement], candidates: list[RootCauseCandidate],
    previous: InvestigationPlan | None = None,
    *, skills: SkillCatalog | None = None,
    capabilities: dict[str, list[str]] | None = None,
) -> InvestigationPlan:
    """Keep task identity stable while new observations alter readiness and priority."""
    version = (previous.version if previous else 0) + 1
    old = {task.task_id: task for task in previous.tasks} if previous else {}
    ranked = {candidate.root_cause_type.value: index for index, candidate in enumerate(candidates)}
    tasks: dict[str, InvestigationTask] = {}
    for requirement in requirements:
        if not requirement.active:
            continue
        required_tools = list(dict.fromkeys(requirement.suggested_tools))
        domain = next((label for prefix, label in TOOL_DOMAINS.items()
                       if any(tool.startswith(prefix) for tool in required_tools)), "general")
        if domain != "general":
            required_tools = [tool for tool in required_tools if tool.startswith(domain + ".")]
        # The requirement's direct measurement comes first. Domain alternatives
        # are authorized fallback reads when that measurement fails or has no data.
        tools = list(dict.fromkeys([
            *required_tools, *(capabilities or {}).get(domain, []),
        ])) if required_tools else []
        selected_skill = None
        skill_version = None
        skill_loaded_bytes = 0
        skill_reason = None
        skill_tool_order = []
        if skills is not None and tools and not requirement.satisfied:
            match = skills.select(domain=domain, signal=requirement.signal)
            if match:
                try:
                    activated = skills.activate(match.name, reason=requirement.reason, allowed_tools=tools)
                except (OSError, UnicodeError, KeyError):
                    activated = None  # retain the requirement's authorized tools
                if activated is not None:
                    selected_skill = activated.name
                    skill_version = activated.version
                    skill_loaded_bytes = activated.loaded_bytes
                    skill_reason = activated.reason
                    skill_tool_order = list(activated.tool_order)
                    if len(required_tools) == 1:
                        primary = required_tools
                        secondary = [tool for tool in activated.tool_order if tool not in primary]
                        tools = [*primary, *secondary, *(tool for tool in tools if tool not in primary + secondary)]
                    else:
                        tools = [*activated.tool_order, *(tool for tool in tools if tool not in activated.tool_order)]
        candidate = next((name for name in ranked if name.startswith(domain + "_")), None)
        identity = requirement.requirement_id
        prior = old.get(identity)
        if requirement.satisfied:
            status, reason = TaskStatus.SATISFIED, "required observation is covered"
        elif not tools:
            status, reason = TaskStatus.BLOCKED, "no authorized tool can measure this signal"
        else:
            status, reason = TaskStatus.READY, "observation required"
        priority = (100 if requirement.blocking else 50) - ranked.get(candidate, 10)
        history = list(prior.history) if prior else []
        if prior is None or prior.status != status or prior.priority != priority:
            history.append(TaskEvent(version=version, status=status, reason=reason))
        tasks[identity] = InvestigationTask(
            task_id=identity, objective=requirement.purpose,
            target_service=requirement.service, domain=domain, candidate_cause=candidate,
            required_signal=requirement.signal, priority=priority, assigned_agent=domain,
            allowed_tools=tools, selected_skill=selected_skill, status=status, history=history,
            skill_version=skill_version, skill_loaded_bytes=skill_loaded_bytes,
            skill_reason=skill_reason, skill_tool_order=skill_tool_order,
            attempts=prior.attempts if prior else 0,
            result_observation_ids=requirement.observation_ids,
            stop_reason=reason if status == TaskStatus.BLOCKED else None,
        )
    for identity, prior in old.items():
        if identity not in tasks:
            tasks[identity] = prior.model_copy(update={
                "status": TaskStatus.CANCELLED, "stop_reason": "requirement no longer active",
                "history": [*prior.history, TaskEvent(
                    version=version, status=TaskStatus.CANCELLED,
                    reason="requirement no longer active")],
            })
    return InvestigationPlan(version=version, tasks=sorted(
        tasks.values(), key=lambda task: (task.status != TaskStatus.READY, -task.priority, task.task_id)))

"""Bounded, independent investigation of one evidence requirement."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opspilot.config import RuntimeSettings
from opspilot.investigation.analysis import DeterministicEvidenceEngine
from opspilot.investigation.tasks import InvestigationTask, TaskStatus
from opspilot.llm import DeepSeekRCAClient, DeepSeekSecrets
from opspilot.models import AlertEvent, Evidence, RootCauseType, ToolResult, ToolStatus
from opspilot.tools import ToolRegistry

SpecialistToolRunner = Callable[[str, int, str, str | None], Awaitable[ToolResult]]
SpecialistJSONCall = Callable[..., Awaitable[dict[str, Any]]]


class SpecialistDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["inspect", "stop"]
    tool_name: str | None = None
    reason: str = Field(min_length=1, max_length=500)
    llm_used: bool = False


class SpecialistContext(BaseModel):
    """A task-local view; other agents' mutable state is not shared."""

    model_config = ConfigDict(extra="forbid")
    alert: AlertEvent
    task: InvestigationTask
    prior_evidence: list[Evidence] = Field(default_factory=list)
    round_number: int = Field(ge=1)
    deadline: datetime | None = None
    token_budget: int | None = Field(default=None, ge=0)


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
    local_decisions: list[SpecialistDecision] = Field(default_factory=list)
    llm_used: bool = False
    fallback_reason: str | None = None
    token_usage: int = Field(default=0, ge=0)


class SpecialistAgent:
    """Use scoped tools in local priority order and reassess after each read.

    This deterministic fallback is usable when an LLM is unavailable. The
    supervisor owns task assignment, budgets and final diagnosis.
    """

    def __init__(self, registry: ToolRegistry, *, json_call: SpecialistJSONCall | None = None) -> None:
        self.registry = registry
        self.analysis = DeterministicEvidenceEngine()
        self.json_call = json_call

    @classmethod
    def from_settings(cls, registry: ToolRegistry, settings: RuntimeSettings) -> SpecialistAgent:
        if not settings.llm_enabled:
            return cls(registry)
        try:
            key = DeepSeekSecrets().deepseek_api_key
            if not key.get_secret_value():
                return cls(registry)
        except ValidationError:
            return cls(registry)
        client = DeepSeekRCAClient(api_key=key, model=settings.llm_model,
                                   base_url=settings.llm_base_url,
                                   timeout_seconds=settings.llm_timeout_seconds, max_attempts=1)
        return cls(registry, json_call=client.complete_json_with_usage)

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
        decisions: list[SpecialistDecision] = []
        llm_used = False
        fallback_reason = None
        token_usage = 0
        stopped_by_decision = False
        candidate = next((cause for cause in RootCauseType if cause.value == task.candidate_cause), None)
        stop_reason = "tool budget exhausted"
        for _ in range(min(max_tool_calls, len(allowed))):
            if context.deadline is not None and datetime.now(UTC) >= context.deadline:
                stop_reason = "time budget exhausted before next measurement"
                break
            remaining = [name for name in allowed if name not in {item.tool_name for item in observed}]
            budget = None if context.token_budget is None else max(0, context.token_budget - token_usage)
            decision, used_llm, fallback, used_tokens = await self._choose(
                context, observed, remaining, token_budget=budget)
            decisions.append(decision)
            llm_used |= used_llm
            fallback_reason = fallback or fallback_reason
            token_usage += used_tokens
            if decision.action == "stop":
                stop_reason = decision.reason
                stopped_by_decision = True
                break
            name = decision.tool_name
            assert name is not None
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
            suggested_tools=remaining if unresolved and not stopped_by_decision else [],
            tool_calls=len(observed), stop_reason=stop_reason,
            local_decisions=decisions, llm_used=llm_used, fallback_reason=fallback_reason,
            token_usage=token_usage,
        )

    async def _choose(self, context: SpecialistContext, observed: list[ToolResult],
                      remaining: list[str], *, token_budget: int | None,
                      ) -> tuple[SpecialistDecision, bool, str | None, int]:
        if self.json_call is not None:
            try:
                system_prompt = ("You are a read-only domain RCA specialist. Choose inspect with one allowed "
                                 "tool_name, or stop. Return action, tool_name, reason. Use only the scoped "
                                 "observations supplied. Do not decide the final root cause or verdict.")
                payload = {
                        "task": {"objective": context.task.objective, "domain": context.task.domain,
                                 "target_service": context.task.target_service,
                                 "required_signal": context.task.required_signal},
                        "allowed_tools": remaining,
                        "prior_evidence": [{"type": item.evidence_type, "fact": item.fact}
                                           for item in context.prior_evidence[:8]],
                        "local_observations": [{"tool": item.tool_name, "status": item.status.value,
                                                "error_code": item.error_code} for item in observed],
                    }
                # UTF-8 byte length bounds byte-level input tokenization; reserve
                # the client's 500-token completion cap plus message overhead.
                reserved = len((system_prompt + json.dumps(payload, ensure_ascii=False)).encode("utf-8")) + 628
                if token_budget is not None and reserved > token_budget:
                    raise ValueError("LLM token budget exhausted")
                raw = await self.json_call(system_prompt=system_prompt, payload=payload)
                used_tokens = 0
                if "decision" in raw and "token_usage" in raw:
                    used_tokens = int(raw["token_usage"])
                    if used_tokens < 0:
                        raise ValueError("negative LLM token usage")
                    raw = raw["decision"]
                decision = SpecialistDecision.model_validate(raw)
                if decision.action == "inspect" and decision.tool_name not in remaining:
                    raise ValueError("LLM selected a tool outside the specialist whitelist")
                if decision.action == "stop" and decision.tool_name is not None:
                    raise ValueError("stop decision must not include a tool")
                return decision.model_copy(update={"llm_used": True}), True, None, used_tokens
            except Exception as exc:  # noqa: BLE001 - preserve deterministic fallback
                fallback = f"{type(exc).__name__}: {exc}"
        else:
            fallback = None
        return SpecialistDecision(action="inspect", tool_name=remaining[0],
                                  reason="next authorized measurement"), False, fallback, 0

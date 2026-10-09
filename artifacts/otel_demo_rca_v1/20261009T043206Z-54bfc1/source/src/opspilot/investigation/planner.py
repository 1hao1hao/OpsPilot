"""Constrained LLM Adaptive Planner, deterministic fallback, and central action authorization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from opspilot.config import RuntimeSettings
from opspilot.llm import DeepSeekRCAClient, DeepSeekSecrets
from opspilot.models import (
    AlertEvent,
    Evidence,
    EvidenceGateDecision,
    InvestigationAction,
    InvestigationActionType,
    RootCauseCandidate,
    RootCauseType,
)
from opspilot.tools import ToolRegistry

DOMAIN_TOOLS = {
    "db": ["db.replication", "db.slowlog", "db.connections"],
    "redis": ["redis.memory", "redis.hotkeys"],
    "kafka": ["kafka.lag"],
    "rpc": ["rpc.metrics"],
}

GENERAL_TOOL_PRIORITY = (
    "metrics.query",
    "logs.query",
    "traces.query",
    "changes.query",
    "topology.query",
    "alerts.query",
)

PlannerJSONCall = Callable[..., Awaitable[dict[str, Any]]]


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_tool", "invoke_expert"]
    target: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class ActionValidator:
    """The central code-level authorization boundary for Planner and Expert actions."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def validate_planner_action(self, action: InvestigationAction) -> InvestigationAction:
        if action.action_type == InvestigationActionType.INSPECT_TOOL:
            if action.target not in self.registry.general_names():
                raise ValueError(f"Adaptive Planner cannot inspect non-general Tool: {action.target}")
        elif action.action_type == InvestigationActionType.INVOKE_EXPERT:
            if action.target not in DOMAIN_TOOLS:
                raise ValueError(f"Adaptive Planner selected unknown Expert: {action.target}")
        else:
            raise ValueError("Adaptive Planner may only inspect_tool or invoke_expert")
        return action

    def validate_seed_tool(self, tool_name: str) -> None:
        if tool_name not in self.registry.general_names():
            raise ValueError(f"Seed Planner cannot select non-general Tool: {tool_name}")

    def validate_expert_tool(self, domain: str, tool_name: str) -> None:
        if tool_name not in self.registry.domain_names(domain):
            raise ValueError(f"{domain} Expert cannot execute Tool: {tool_name}")


class EvidenceGate:
    def __init__(self, *, confidence: float, margin: float, min_sources: int) -> None:
        self.confidence = confidence
        self.margin = margin
        self.min_sources = min_sources

    def evaluate(
        self,
        candidates: list[RootCauseCandidate],
        evidence: list[Evidence],
        *,
        budget_exhausted: bool = False,
    ) -> EvidenceGateDecision:
        top1 = candidates[0]
        second = candidates[1].confidence if len(candidates) > 1 else 0.0
        margin = max(top1.confidence - second, 0.0)
        sources = {
            item.source_group
            for item in evidence
            if item.source_group and top1.root_cause_type in item.supports and item.evidence_id in top1.evidence_ids
        }
        sufficient = (
            top1.root_cause_type != RootCauseType.NO_FAULT
            and top1.confidence >= self.confidence
            and margin >= self.margin
            and len(sources) >= self.min_sources
        )
        if sufficient:
            reason = (
                f"top1={top1.root_cause_type.value} confidence={top1.confidence:.3f}, "
                f"margin={margin:.3f}, independent_sources={len(sources)}"
            )
        elif budget_exhausted:
            reason = "investigation budget exhausted; use current complete deterministic ranking"
        else:
            reason = (
                f"evidence insufficient: confidence={top1.confidence:.3f}, "
                f"margin={margin:.3f}, independent_sources={len(sources)}"
            )
        return EvidenceGateDecision(
            sufficient=sufficient,
            reason=reason,
            top1_confidence=top1.confidence,
            score_margin=margin,
            independent_source_count=len(sources),
            budget_exhausted=budget_exhausted,
        )


class DeterministicPlannerFallback:
    """Evidence-linked Expert first, otherwise a fixed order of unused general Tools."""

    def __init__(self, registry: ToolRegistry, validator: ActionValidator) -> None:
        self.registry = registry
        self.validator = validator

    def decide(
        self,
        *,
        alert: AlertEvent,
        round_number: int,
        evidence: list[Evidence],
        candidates: list[RootCauseCandidate],
        executed_tools: list[str],
        invoked_experts: list[str],
        action_history: list[InvestigationAction],
        action_identities: set[str],
        remaining_round_budget: int,
        remaining_tool_budget: int,
        remaining_expert_budget: int,
    ) -> InvestigationAction | None:
        del action_history, candidates
        if remaining_tool_budget <= 0 or remaining_round_budget <= 0:
            return None
        domains = {cause.value.split("_", 1)[0] for item in evidence for cause in item.supports}
        choices = []
        if remaining_expert_budget > 0:
            choices.extend(
                (InvestigationActionType.INVOKE_EXPERT, domain, "Evidence supports " + domain)
                for domain in DOMAIN_TOOLS
                if domain in domains and domain not in invoked_experts and self.registry.domain_names(domain)
            )
        general = self.registry.general_names()
        choices.extend(
            (InvestigationActionType.INSPECT_TOOL, name, "Next unexecuted general observation")
            for name in dict.fromkeys((*GENERAL_TOOL_PRIORITY, *general))
            if name in general and name not in executed_tools
        )
        for action_type, target, reason in choices:
            action = InvestigationAction(
                action_type=action_type,
                target=target,
                reason=reason,
                round=round_number,
                arguments={"service_name": alert.service_name}
                if action_type == InvestigationActionType.INSPECT_TOOL
                else {},
            )
            if action.identity not in action_identities:
                return self.validator.validate_planner_action(action)
        return None


class LLMAdaptivePlanner:
    """Use DeepSeek whenever enabled; any failure falls back deterministically."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        llm_enabled: bool = False,
        json_call: PlannerJSONCall | None = None,
    ) -> None:
        self.registry = registry
        self.validator = ActionValidator(registry)
        self.fallback = DeterministicPlannerFallback(registry, self.validator)
        self.llm_enabled = llm_enabled
        self.json_call = json_call
        self.last_used_llm = False
        self.last_fallback_reason: str | None = None

    @classmethod
    def from_settings(
        cls,
        registry: ToolRegistry,
        settings: RuntimeSettings,
        *,
        json_call: PlannerJSONCall | None = None,
    ) -> LLMAdaptivePlanner:
        if json_call is not None:
            return cls(registry, llm_enabled=settings.llm_enabled, json_call=json_call)
        if not settings.llm_enabled:
            return cls(registry)
        secrets = DeepSeekSecrets()
        client = DeepSeekRCAClient(
            api_key=SecretStr(secrets.deepseek_api_key.get_secret_value()),
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
            max_attempts=1,
        )
        return cls(registry, llm_enabled=True, json_call=client.complete_json)

    async def decide(self, **kwargs) -> InvestigationAction | None:
        self.last_used_llm = False
        self.last_fallback_reason = None
        if kwargs["remaining_round_budget"] <= 0 or kwargs["remaining_tool_budget"] <= 0:
            return None
        if self.llm_enabled and self.json_call is not None:
            try:
                raw = await self.json_call(
                    system_prompt=(
                        "You control an RCA investigation. Return exactly action, target, reason. "
                        "action is inspect_tool or invoke_expert. Select only from allowed_actions. "
                        "Prefer measurements of observed dependencies to repeatedly collecting generic symptoms. "
                        "Slow spans and dependency hints alone do not prove timeouts or domain faults. "
                        "Never finalize and never infer facts not present in the input."
                    ),
                    payload=self._payload(**kwargs),
                )
                decision = PlannerDecision.model_validate(raw)
                action = InvestigationAction(
                    action_type=InvestigationActionType(decision.action),
                    target=decision.target,
                    reason=decision.reason,
                    round=kwargs["round_number"],
                    arguments=(
                        {"service_name": kwargs["alert"].service_name} if decision.action == "inspect_tool" else {}
                    ),
                )
                action = self.validator.validate_planner_action(action)
                if action.identity in kwargs["action_identities"]:
                    raise ValueError("LLM returned a duplicate action")
                if action.target not in self._payload(**kwargs)["allowed_actions"][decision.action]:
                    raise ValueError("LLM returned an unavailable or budget-exhausted action")
                self.last_used_llm = True
                return action
            except Exception as exc:  # noqa: BLE001 - fallback is the reliability contract
                self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
        return self.fallback.decide(**kwargs)

    def _payload(self, **kwargs) -> dict[str, Any]:
        alert: AlertEvent = kwargs["alert"]
        # A large number of equivalent spans must not hide other measurements
        # or dependency hints from the planner's bounded context.
        diverse = {}
        for item in kwargs["evidence"]:
            key = (item.source_name, item.evidence_type, tuple(item.supports))
            diverse.setdefault(key, item)
        return {
            "alert": {
                "service_name": alert.service_name,
                "alert_type": alert.alert_type.value,
                "severity": alert.severity.value,
                "description": alert.description,
                "labels": alert.labels,
            },
            "evidence": [
                {
                    "type": item.evidence_type,
                    "fact": item.fact,
                    "confidence": item.confidence,
                    "source_group": item.source_group,
                    "supports": [cause.value for cause in item.supports],
                }
                for item in list(diverse.values())[:20]
            ],
            "provisional_top_k": [
                {"root_cause_type": item.root_cause_type.value, "confidence": item.confidence}
                for item in kwargs["candidates"][:3]
            ],
            "executed_tools": kwargs["executed_tools"],
            "invoked_experts": kwargs["invoked_experts"],
            "action_history": [
                {"action": item.action_type.value, "target": item.target, "reason": item.reason}
                for item in kwargs["action_history"]
            ],
            "remaining_budget": {
                "rounds": kwargs["remaining_round_budget"],
                "tools": kwargs["remaining_tool_budget"],
                "experts": kwargs["remaining_expert_budget"],
            },
            "allowed_actions": {
                "inspect_tool": [
                    name for name in self.registry.general_names() if name not in kwargs["executed_tools"]
                ],
                "invoke_expert": [
                    name
                    for name in DOMAIN_TOOLS
                    if name not in kwargs["invoked_experts"]
                    and kwargs["remaining_expert_budget"] > 0
                    and self.registry.domain_names(name)
                ],
            },
        }

    def expert_tools(
        self,
        domain: str,
        alert: AlertEvent,
        evidence: list[Evidence],
    ) -> list[str]:
        del alert
        # Select only tools whose causes are supported; without a clue, inspect
        # the domain's small fixed tool set. Selection never diagnoses a cause.
        cause_tools = {
            RootCauseType.DB_REPLICATION_LAG: "db.replication",
            RootCauseType.DB_SLOW_QUERY: "db.slowlog",
            RootCauseType.DB_CONNECTION_EXHAUSTED: "db.connections",
            RootCauseType.REDIS_MEMORY_PRESSURE: "redis.memory",
            RootCauseType.REDIS_LOW_HIT_RATE: "redis.hotkeys",
            RootCauseType.KAFKA_CONSUMER_LAG: "kafka.lag",
            RootCauseType.RPC_TIMEOUT: "rpc.metrics",
            RootCauseType.RPC_ERROR_RATE: "rpc.metrics",
        }
        supported = {cause_tools[cause] for item in evidence for cause in item.supports if cause in cause_tools}
        tools = DOMAIN_TOOLS[domain]
        selected = [name for name in tools if name in supported] or tools
        return [name for name in selected if name in self.registry.domain_names(domain)]

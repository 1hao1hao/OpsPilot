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
    SemanticAnalysisResult,
)
from opspilot.tools import ToolRegistry

DOMAIN_TOOLS = {
    "db": ["db.replication", "db.slowlog", "db.connections"],
    "redis": ["redis.memory", "redis.hotkeys"],
    "kafka": ["kafka.lag"],
    "rpc": ["rpc.metrics"],
}

DOMAIN_HINTS = {
    "db": (
        "mysql",
        "postgres",
        "database",
        "persistence",
        "replica",
        "read request",
        "slow query",
        "connection",
        "storage",
    ),
    "redis": ("redis", "cache", "hotkey", "fallback", "backend load"),
    "kafka": (
        "kafka",
        "consumer",
        "message",
        "queue",
        "event",
        "background work",
        "async",
        "processing throughput",
        "processing delay",
    ),
    "rpc": ("rpc", "grpc", "dependency", "downstream", "payment", "http"),
}

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
            if item.source_group
            and top1.root_cause_type in item.supports
            and item.evidence_id in top1.evidence_ids
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
    """Reliable fallback that consumes only public Alert fields and observed analysis results."""

    def __init__(self, registry: ToolRegistry, validator: ActionValidator) -> None:
        self.registry = registry
        self.validator = validator

    def decide(
        self,
        *,
        alert: AlertEvent,
        round_number: int,
        dimension_results: list[SemanticAnalysisResult],
        expert_results: list[SemanticAnalysisResult],
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
        del action_history, remaining_round_budget
        if remaining_tool_budget <= 0:
            return None
        context = self._context(alert, dimension_results, expert_results, evidence, candidates)
        change_hints = ("deploy", "release", "version", "config change", "changed in the incident")
        if "changes.query" not in executed_tools and any(token in context for token in change_hints):
            action = InvestigationAction(
                action_type=InvestigationActionType.INSPECT_TOOL,
                target="changes.query",
                reason="public alert context indicates a recent change",
                round=round_number,
                arguments={"service_name": alert.service_name},
            )
            if action.identity not in action_identities:
                return self.validator.validate_planner_action(action)
        domain = self._next_domain(alert, context, invoked_experts)
        if domain and remaining_expert_budget > 0:
            action = InvestigationAction(
                action_type=InvestigationActionType.INVOKE_EXPERT,
                target=domain,
                reason=f"deterministic fallback matched observed/public context for {domain}: {context[:180]}",
                round=round_number,
            )
            if action.identity not in action_identities:
                return self.validator.validate_planner_action(action)

        for tool_name, reason in self._supplementary_tools(alert):
            if tool_name not in self.registry.general_names() or tool_name in executed_tools:
                continue
            action = InvestigationAction(
                action_type=InvestigationActionType.INSPECT_TOOL,
                target=tool_name,
                reason=reason,
                round=round_number,
                arguments={"service_name": alert.service_name},
            )
            if action.identity not in action_identities:
                return self.validator.validate_planner_action(action)
        return None

    @staticmethod
    def _context(
        alert: AlertEvent,
        dimension_results: list[SemanticAnalysisResult],
        expert_results: list[SemanticAnalysisResult],
        evidence: list[Evidence],
        candidates: list[RootCauseCandidate],
    ) -> str:
        findings = " ".join(
            f"{finding.summary} {finding.data.get('path', '')} {finding.service}"
            for result in dimension_results + expert_results
            for finding in result.findings
        )
        labels = " ".join(f"{key} {value}" for key, value in alert.labels.items())
        facts = " ".join(item.fact for item in evidence[:10])
        top = " ".join(item.root_cause_type.value for item in candidates[:3])
        # alert.signals is intentionally absent: it is a Tool backend snapshot.
        # Keep service_name in the LLM payload, but do not treat a business service name
        # (for example payment-service) as evidence for a matching Expert domain.
        return f" {alert.alert_type.value} {alert.description} {labels} {findings} {facts} {top} ".lower()

    @staticmethod
    def _next_domain(alert: AlertEvent, context: str, invoked_experts: list[str]) -> str | None:
        for domain, hints in DOMAIN_HINTS.items():
            if domain not in invoked_experts and any(hint in context for hint in hints):
                return domain
        defaults = {
            "timeout": ("rpc", "db"),
            "error_rate": ("rpc", "redis", "kafka", "db"),
            "resource": ("db", "redis", "kafka"),
            "custom": ("kafka", "db", "redis", "rpc"),
        }
        return next((domain for domain in defaults[alert.alert_type.value] if domain not in invoked_experts), None)

    @staticmethod
    def _supplementary_tools(alert: AlertEvent) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        if alert.alert_type.value in {"timeout", "error_rate"}:
            items.append(("topology.query", "inspect dependency context after inconclusive evidence"))
        if alert.severity.value in {"P0", "P1"}:
            items.append(("alerts.query", "correlate high-severity alert with related incidents"))
        items.extend([
            ("changes.query", "check recent changes after inconclusive evidence"),
            ("logs.query", "check error signatures after inconclusive evidence"),
        ])
        return items


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
        if self.llm_enabled and self.json_call is not None:
            try:
                raw = await self.json_call(
                    system_prompt=(
                        "You control an RCA investigation. Return exactly action, target, reason. "
                        "action is inspect_tool or invoke_expert. Select only from allowed_actions. "
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
                        {"service_name": kwargs["alert"].service_name}
                        if decision.action == "inspect_tool"
                        else {}
                    ),
                )
                action = self.validator.validate_planner_action(action)
                if action.identity in kwargs["action_identities"]:
                    raise ValueError("LLM returned a duplicate action")
                self.last_used_llm = True
                return action
            except Exception as exc:  # noqa: BLE001 - fallback is the reliability contract
                self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
        return self.fallback.decide(**kwargs)

    def _payload(self, **kwargs) -> dict[str, Any]:
        alert: AlertEvent = kwargs["alert"]
        return {
            "alert": {
                "service_name": alert.service_name,
                "alert_type": alert.alert_type.value,
                "severity": alert.severity.value,
                "description": alert.description,
                "labels": alert.labels,
            },
            "l1_findings": self._findings(kwargs["dimension_results"]),
            "l2_findings": self._findings(kwargs["expert_results"]),
            "evidence": [
                {"type": item.evidence_type, "fact": item.fact, "confidence": item.confidence}
                for item in kwargs["evidence"][:20]
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
                "invoke_expert": [name for name in DOMAIN_TOOLS if name not in kwargs["invoked_experts"]],
            },
        }

    @staticmethod
    def _findings(results: list[SemanticAnalysisResult]) -> list[dict[str, Any]]:
        return [
            {
                "type": finding.finding_type,
                "service": finding.service,
                "summary": finding.summary,
                "confidence": finding.confidence,
            }
            for result in results
            for finding in result.findings
        ]

    def expert_tools(
        self,
        domain: str,
        alert: AlertEvent,
        dimension_results: list[SemanticAnalysisResult],
        evidence: list[Evidence],
    ) -> list[str]:
        context = DeterministicPlannerFallback._context(alert, dimension_results, [], evidence, [])
        if domain == "db":
            selected = []
            if any(token in context for token in ("replica", "read", "mysql")):
                selected.append("db.replication")
            if any(token in context for token in ("slow", "query", "mysql", "persistence")):
                selected.append("db.slowlog")
            if any(token in context for token in ("connection", "queue", "capacity", "resource", "storage", "waiting")):
                selected.append("db.connections")
            if selected:
                candidates = selected
            elif alert.alert_type.value == "resource":
                candidates = ["db.connections"]
            elif alert.alert_type.value == "timeout":
                # Timeout alone cannot distinguish read-replica lag from a slow query.
                candidates = ["db.replication", "db.slowlog"]
            else:
                candidates = ["db.slowlog"]
        elif domain == "redis":
            selected = []
            if any(token in context for token in ("memory", "capacity", "resource")):
                selected.append("redis.memory")
            if any(token in context for token in ("hit", "hotkey", "fallback", "backend load")):
                selected.append("redis.hotkeys")
            candidates = selected or ["redis.memory"]
        else:
            candidates = DOMAIN_TOOLS[domain]
        return [name for name in candidates if name in self.registry.domain_names(domain)]


# Compatibility import for callers that used the v3 name.
AdaptivePlanner = LLMAdaptivePlanner

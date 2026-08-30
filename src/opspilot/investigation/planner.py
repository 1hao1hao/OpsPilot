"""Deterministic Adaptive Planner with a strict action whitelist."""

from __future__ import annotations

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
    "db": ("mysql", "postgres", "database", " db ", "persistence", "replica", "slow query", "connection"),
    "redis": ("redis", "cache", "hotkey"),
    "kafka": ("kafka", "consumer", "message", "queue", "event delivery"),
    "rpc": ("rpc", "grpc", "dependency", "downstream", "payment", "http"),
}


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
            item.source_name
            for item in evidence
            if top1.root_cause_type in item.supports and item.evidence_id in top1.evidence_ids
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
                f"margin={margin:.3f}, sources={len(sources)}"
            )
        elif budget_exhausted:
            reason = "investigation budget exhausted; force deterministic finalize"
        else:
            reason = (
                f"evidence insufficient: confidence={top1.confidence:.3f}, "
                f"margin={margin:.3f}, sources={len(sources)}"
            )
        return EvidenceGateDecision(
            sufficient=sufficient,
            reason=reason,
            top1_confidence=top1.confidence,
            score_margin=margin,
            independent_source_count=len(sources),
            budget_exhausted=budget_exhausted,
        )


class AdaptivePlanner:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def decide(
        self,
        *,
        alert: AlertEvent,
        round_number: int,
        dimension_results: list[SemanticAnalysisResult],
        executed_tools: list[str],
        invoked_experts: list[str],
        action_identities: set[str],
        gate: EvidenceGateDecision,
        remaining_tool_budget: int,
        remaining_expert_budget: int,
    ) -> InvestigationAction:
        if gate.sufficient:
            return self._finalize(round_number, gate.reason)
        if remaining_tool_budget <= 0 or remaining_expert_budget < 0:
            return self._finalize(round_number, "budget exhausted")

        domain, reason = self._next_domain(alert, dimension_results, invoked_experts)
        if domain and remaining_expert_budget > 0:
            action = InvestigationAction(
                action_type=InvestigationActionType.INVOKE_EXPERT,
                target=domain,
                reason=reason,
                round=round_number,
            )
            if action.identity not in action_identities:
                return self.validate(action)

        supplementary = self._supplementary_tools(alert)
        for tool_name, tool_reason in supplementary:
            if tool_name not in self.registry.names():
                continue
            action = InvestigationAction(
                action_type=InvestigationActionType.INSPECT_TOOL,
                target=tool_name,
                reason=tool_reason,
                round=round_number,
                arguments={"service_name": alert.service_name},
            )
            if tool_name not in executed_tools and action.identity not in action_identities:
                return self.validate(action)
        return self._finalize(round_number, "no new non-duplicate investigation action")

    def expert_tools(
        self,
        domain: str,
        alert: AlertEvent,
        dimension_results: list[SemanticAnalysisResult],
    ) -> list[str]:
        candidates = DOMAIN_TOOLS[domain]
        context = self._context(alert, dimension_results)
        if domain == "db":
            selected: list[str] = []
            if any(token in context for token in ("replica", "read", "mysql")):
                selected.append("db.replication")
            if any(token in context for token in ("slow", "latency", "query", "mysql")):
                selected.append("db.slowlog")
            if any(token in context for token in ("connection", "queue", "capacity", "resource")):
                selected.append("db.connections")
            candidates = selected or candidates
        return [name for name in candidates if name in self.registry.names()]

    def validate(self, action: InvestigationAction) -> InvestigationAction:
        if action.action_type == InvestigationActionType.INSPECT_TOOL and action.target not in self.registry.names():
            raise ValueError(f"planner selected unknown Tool: {action.target}")
        if action.action_type == InvestigationActionType.INVOKE_EXPERT and action.target not in DOMAIN_TOOLS:
            raise ValueError(f"planner selected unknown Expert: {action.target}")
        if action.action_type == InvestigationActionType.FINALIZE and action.target != "finalize":
            raise ValueError("finalize action target must be 'finalize'")
        return action

    def _next_domain(
        self,
        alert: AlertEvent,
        dimension_results: list[SemanticAnalysisResult],
        invoked_experts: list[str],
    ) -> tuple[str | None, str]:
        context = self._context(alert, dimension_results)
        for domain, hints in DOMAIN_HINTS.items():
            if domain not in invoked_experts and any(hint in context for hint in hints):
                return domain, f"observed context matched {domain}: {context[:180]}"

        # Signal names describe available observation sources, not their values.
        # They are a deterministic fallback only after the seed round failed to
        # produce enough evidence.
        for domain in DOMAIN_TOOLS:
            if domain in alert.signals and domain not in invoked_experts:
                return domain, f"seed evidence insufficient and {domain} observation source is available"
        return None, "no domain hint"

    @staticmethod
    def _context(alert: AlertEvent, dimension_results: list[SemanticAnalysisResult]) -> str:
        findings = " ".join(
            f"{finding.summary} {finding.data.get('path', '')} {finding.service}"
            for result in dimension_results
            for finding in result.findings
        )
        labels = " ".join(f"{key} {value}" for key, value in alert.labels.items())
        signals = " ".join(
            f"{source} {key} {value}"
            for source, payload in alert.signals.items()
            for key, value in payload.items()
        )
        return f" {alert.description} {labels} {signals} {findings} ".lower()

    @staticmethod
    def _supplementary_tools(alert: AlertEvent) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        if alert.alert_type.value in {"timeout", "error_rate"}:
            items.append(("topology.query", "inspect dependency context after inconclusive seed evidence"))
        if alert.severity.value in {"P0", "P1"}:
            items.append(("alerts.query", "correlate high-severity alert with related incidents"))
        items.extend(
            [
                ("changes.query", "check recent changes after inconclusive evidence"),
                ("logs.query", "check error signatures after inconclusive evidence"),
            ]
        )
        return items

    def _finalize(self, round_number: int, reason: str) -> InvestigationAction:
        return self.validate(
            InvestigationAction(
                action_type=InvestigationActionType.FINALIZE,
                target="finalize",
                reason=reason,
                round=round_number,
            )
        )

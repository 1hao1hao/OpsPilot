"""One deterministic analysis path shared by every investigation round and the final report."""

from __future__ import annotations

from opspilot.agents import RootCauseAgent, analyze_dimensions, analyze_experts
from opspilot.evidence import collect_evidence, collect_expert_evidence, collect_semantic_evidence
from opspilot.models import AlertEvent, AnalysisPlan, Evidence, RoundAnalysisResult, ToolResult
from opspilot.rca.pipeline import run_deterministic_pipeline


class DeterministicEvidenceEngine:
    """Build the complete Evidence Pool and ranking exactly once per round."""

    def __init__(
        self,
        *,
        ranker: RootCauseAgent | None = None,
        promote_expert_evidence: bool = True,
    ) -> None:
        self.ranker = ranker or RootCauseAgent()
        self.promote_expert_evidence = promote_expert_evidence
        self.analysis_count = 0

    def analyze(
        self,
        alert: AlertEvent,
        plan: AnalysisPlan,
        tool_results: list[ToolResult],
        selected_experts: list[str],
    ) -> RoundAnalysisResult:
        self.analysis_count += 1
        dimensions = analyze_dimensions(alert, plan, tool_results)
        experts = analyze_experts(alert, tool_results, domains=selected_experts)
        evidence = collect_evidence(alert, tool_results)
        evidence.extend(collect_semantic_evidence(alert, dimensions))
        if self.promote_expert_evidence:
            evidence.extend(collect_expert_evidence(alert, experts))
        evidence = self._deduplicate(evidence)

        algorithm_signals, deterministic_evidence, matched_rules = run_deterministic_pipeline(
            alert,
            tool_results,
            dimensions,
            experts,
            evidence,
        )
        evidence.extend(deterministic_evidence)
        evidence = self._deduplicate(evidence)
        candidates, _ = self.ranker.diagnose(alert, evidence)
        return RoundAnalysisResult(
            dimension_results=dimensions,
            expert_results=experts,
            algorithm_signals=algorithm_signals,
            matched_rules=matched_rules,
            evidence=evidence,
            candidates=candidates,
        )

    @staticmethod
    def _deduplicate(items: list[Evidence]) -> list[Evidence]:
        return sorted(
            {item.evidence_id: item for item in items}.values(),
            key=lambda item: (-item.confidence, item.evidence_id),
        )

"""The shared ToolResult -> Evidence -> deterministic ranking path."""

from opspilot.agents import RootCauseAgent
from opspilot.evidence import collect_evidence
from opspilot.models import AlertEvent, RoundAnalysisResult, ToolResult
from opspilot.rca import AnomalyDetector


class DeterministicEvidenceEngine:
    def __init__(self, *, ranker: RootCauseAgent | None = None) -> None:
        self.ranker = ranker or RootCauseAgent()
        self.detector = AnomalyDetector()
        self.analysis_count = 0

    def analyze(self, alert: AlertEvent, tool_results: list[ToolResult]) -> RoundAnalysisResult:
        self.analysis_count += 1
        evidence = collect_evidence(alert, tool_results)
        evidence.extend(self.detector.detect(alert, tool_results))
        evidence = sorted(
            {item.evidence_id: item for item in evidence}.values(),
            key=lambda item: (-item.confidence, item.evidence_id),
        )
        candidates, _ = self.ranker.diagnose(alert, evidence)
        return RoundAnalysisResult(evidence=evidence, candidates=candidates)

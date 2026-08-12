"""Thin adapters that keep the DeepRCA HTTP response contract during migration."""

from __future__ import annotations

import json

from opspilot.graph import OpsPilotWorkflow
from opspilot.models import AlertEvent, DiagnosisReport
from opspilot.tools import build_default_registry


class LegacyGraphAdapter:
    """Expose ``ainvoke`` so the old route uses the single OpsPilot workflow."""

    def __init__(self) -> None:
        self.workflow = OpsPilotWorkflow(build_default_registry())

    async def ainvoke(self, state: dict) -> dict:
        alert = AlertEvent.model_validate(state["alert"])
        report = await self.workflow.analyze(alert, trace_id=state.get("trace_id"))
        return report_to_legacy_state(report, state)


def report_to_legacy_state(report: DiagnosisReport, state: dict) -> dict:
    candidates = [
        {
            "rank": item.rank,
            "root_cause": item.summary,
            "root_cause_type": item.root_cause_type.value,
            "confidence": item.confidence,
            "evidence_chain": item.evidence_ids,
            "matched_rule": None,
            "source": "opspilot",
        }
        for item in report.candidates
    ]
    primary = candidates[0]
    root_cause = {
        "candidates": candidates,
        "best_candidate": primary,
        "trace_id": report.trace_id,
        "evidence_chain": [item.model_dump(mode="json") for item in report.evidence],
        "llm_used": False,
        "rule_matched": any(item.source_type.value == "rule" for item in report.evidence),
        "suggestions": report.recommended_actions,
    }
    legacy_report = {
        "trace_id": report.trace_id,
        "alert_id": report.alert_id,
        "service_name": report.service_name,
        "severity": state.get("alert", {}).get("severity", "P2"),
        "status": report.status,
        "root_cause": primary["root_cause"],
        "root_cause_type": primary["root_cause_type"],
        "confidence": primary["confidence"],
        "top_candidates": candidates,
        "key_evidence": [item.fact for item in report.evidence[:5]],
        "analysis_duration": report.latency_ms / 1000,
        "dimensions_analyzed": sorted({item.source_type.value for item in report.evidence}),
        "sub_agents_invoked": ["coordinator", "root_cause"],
        "suggestions": report.recommended_actions,
        "timestamp": report.finished_at.isoformat(),
        "degraded": report.degraded,
    }
    return {
        **state,
        "status": "completed",
        "report": json.dumps(legacy_report, ensure_ascii=False),
        "root_cause": root_cause,
        "sub_agent_results": [],
        "collected_evidence": {
            "total": len(report.evidence),
            "top_evidences": [
                {
                    "source": item.source_name,
                    "dimension": item.source_type.value,
                    "finding": item.fact,
                    "confidence": item.confidence,
                    "evidence_type": item.evidence_type,
                }
                for item in report.evidence[:5]
            ],
        },
    }


def get_legacy_graph_adapter() -> LegacyGraphAdapter:
    return LegacyGraphAdapter()


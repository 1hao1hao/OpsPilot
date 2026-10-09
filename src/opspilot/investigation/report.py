"""Build the final business report from an existing investigation outcome."""

from datetime import UTC, datetime

from opspilot.investigation.decision import decide_diagnosis
from opspilot.investigation.engine import InvestigationOutcome
from opspilot.models import (
    AlertEvent,
    DiagnosisReport,
    DiagnosisVerdict,
    EvidenceGateDecision,
    RootCauseType,
    ToolStatus,
)


def build_report(
    *,
    alert: AlertEvent,
    outcome: InvestigationOutcome,
    trace_id: str,
    started_at: datetime,
    rationale: str,
    llm_used: bool,
) -> DiagnosisReport:
    finished_at = datetime.now(UTC)
    failed = sorted(item.tool_name for item in outcome.tool_results if item.status == ToolStatus.ERROR)
    candidates = outcome.provisional_candidates
    gate = outcome.trace.gate_decisions[-1] if outcome.trace.gate_decisions else EvidenceGateDecision(
        sufficient=False, reason="no recorded root gate", top1_confidence=0,
        score_margin=0, independent_source_count=0)
    # Recompute from actual observations rather than trusting restored metadata.
    decision = decide_diagnosis(alert, outcome.tool_results, outcome.evidence, candidates, gate)
    trace = outcome.trace.model_copy(update={"decision": decision})
    return DiagnosisReport(
        trace_id=trace_id,
        alert_id=alert.alert_id,
        service_name=alert.service_name,
        candidates=candidates,
        primary_root_cause=candidates[0],
        evidence=outcome.evidence,
        investigation=trace,
        verdict=decision.verdict,
        decision=decision,
        primary_root_cause_confirmed=decision.root_cause_confirmed,
        confirmed_root_cause=candidates[0] if decision.root_cause_confirmed else None,
        llm_used=llm_used,
        degraded=bool(failed),
        missing_sources=failed,
        decision_rationale=decision.reason + "; provisional ranking: " + rationale,
        recommended_actions=(recommended_actions(candidates[0].root_cause_type)
                             if decision.verdict == DiagnosisVerdict.CONFIRMED
                             else ["Continue monitoring this alert scope and window"]
                             if decision.verdict == DiagnosisVerdict.NO_FAULT
                             else ["Collect missing or conflicting observations before mitigation"]),
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=max((finished_at - started_at).total_seconds() * 1000, 0),
    )


def recommended_actions(cause: RootCauseType) -> list[str]:
    if cause == RootCauseType.NO_FAULT:
        return ["Continue monitoring and collect more data if the alert persists"]
    if cause.value.startswith("db_"):
        return ["Inspect database capacity and recent query changes", "Mitigate the identified database bottleneck"]
    if cause.value.startswith("redis_"):
        return ["Inspect Redis memory, keys and cache policy"]
    if cause.value.startswith("kafka_"):
        return ["Inspect consumer health and lag by partition"]
    if cause.value.startswith("rpc_"):
        return ["Inspect the affected downstream dependency and timeout policy"]
    if cause == RootCauseType.BAD_DEPLOYMENT:
        return ["Review and, after approval, consider rolling back the recent deployment"]
    return ["Inspect service resource limits and recent workload changes"]

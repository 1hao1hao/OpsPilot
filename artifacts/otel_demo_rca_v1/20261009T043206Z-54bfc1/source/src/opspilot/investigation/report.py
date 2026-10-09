"""Build the final business report from an existing investigation outcome."""

from datetime import UTC, datetime

from opspilot.investigation.engine import InvestigationOutcome
from opspilot.models import AlertEvent, DiagnosisReport, RootCauseType, ToolStatus


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
    return DiagnosisReport(
        trace_id=trace_id,
        alert_id=alert.alert_id,
        service_name=alert.service_name,
        candidates=candidates,
        primary_root_cause=candidates[0],
        evidence=outcome.evidence,
        investigation=outcome.trace,
        llm_used=llm_used,
        degraded=bool(failed),
        missing_sources=failed,
        decision_rationale=rationale,
        recommended_actions=recommended_actions(candidates[0].root_cause_type),
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

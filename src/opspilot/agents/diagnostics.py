"""Six-dimension semantic analysis and L2 experts over unified ToolResults."""

from __future__ import annotations

from typing import Any

from opspilot.models import (
    AlertEvent,
    AnalysisPlan,
    DiagnosticFinding,
    EvidenceSeverity,
    SemanticAnalysisResult,
    ToolResult,
    ToolStatus,
)
from opspilot.tracing import detect_span_anomalies


def _observations(results: list[ToolResult]) -> dict[str, dict[str, Any]]:
    return {
        result.tool_name: result.data.get("observations", {})
        for result in results
        if result.status == ToolStatus.SUCCESS and result.data
    }


def _number(data: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = data.get(name, default)
    if isinstance(value, dict):
        value = value.get("current", value.get("value", value.get("usage_ratio", default)))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric_payload(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if isinstance(value, dict):
        return value
    if data.get("metric") == name:
        return data
    return {}


def _series(data: dict[str, Any], name: str) -> list[float]:
    payload = _metric_payload(data, name)
    points = payload.get("data_points", [])
    values: list[float] = []
    for point in points:
        value = point.get("value") if isinstance(point, dict) else point
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _finding(
    alert: AlertEvent,
    dimension: str,
    finding_type: str,
    summary: str,
    *,
    confidence: float,
    severity: EvidenceSeverity = EvidenceSeverity.WARNING,
    service: str | None = None,
    data: dict[str, Any] | None = None,
) -> DiagnosticFinding:
    return DiagnosticFinding(
        finding_type=finding_type,
        dimension=dimension,
        service=service or alert.service_name,
        summary=summary,
        severity=severity,
        confidence=confidence,
        data=data or {},
    )


def _result(name: str, layer: str, dimension: str, findings: list[DiagnosticFinding]) -> SemanticAnalysisResult:
    confidence = max((item.confidence for item in findings), default=0.1)
    return SemanticAnalysisResult(
        name=name,
        layer=layer,
        dimension=dimension,
        findings=findings,
        confidence=confidence,
    )


def analyze_dimensions(
    alert: AlertEvent,
    plan: AnalysisPlan,
    results: list[ToolResult],
) -> list[SemanticAnalysisResult]:
    """Run the six L1 dimensions without issuing any additional I/O."""
    observed = _observations(results)
    metrics = observed.get("metrics.query", {})
    topology = observed.get("topology.query", {})
    output: list[SemanticAnalysisResult] = []

    for task in plan.dimensions:
        findings: list[DiagnosticFinding] = []
        if task.dimension == "change":
            data = observed.get("changes.query", {})
            changes = data.get("changes", [])
            if data.get("recent_deployment") or changes or _number(data, "high_risk_count") >= 1:
                findings.append(_finding(alert, "change", "recent_change", f"{max(len(changes), int(_number(data, 'high_risk_count')))} recent change(s) overlap the incident window", confidence=0.85))

        elif task.dimension == "upstream":
            qps = _series(metrics, "qps") or _series(metrics, "upstream_qps")
            if len(qps) >= 2 and sum(qps[:-1]) / len(qps[:-1]) > 0 and qps[-1] < (sum(qps[:-1]) / len(qps[:-1])) * 0.5:
                findings.append(_finding(alert, "upstream", "upstream_qps_drop", f"Upstream QPS dropped to {qps[-1]:.2f}", confidence=0.8))
            errors = _series(metrics, "error_rate") or _series(metrics, "upstream_error_rate")
            if errors and max(errors) >= 0.05:
                findings.append(_finding(alert, "upstream", "upstream_error_rate", f"Upstream error rate reached {max(errors):.1%}", confidence=0.8))
            if topology.get("upstream"):
                # Topology is context, not an anomaly; keep it inside result metadata only.
                pass

        elif task.dimension == "downstream":
            trace_data = observed.get("traces.query", {})
            for anomaly in detect_span_anomalies(trace_data):
                path = "/".join(anomaly.path)
                kind = "downstream_span_error" if anomaly.is_error else "downstream_span_slow"
                findings.append(
                    _finding(
                        alert,
                        "downstream",
                        kind,
                        f"Trace {anomaly.trace_id} path {path}: status={anomaly.status}, duration={anomaly.duration_ms:.1f}ms",
                        confidence=0.9,
                        severity=EvidenceSeverity.CRITICAL if anomaly.is_error else EvidenceSeverity.WARNING,
                        service=anomaly.service,
                        data={
                            "trace_id": anomaly.trace_id,
                            "span_id": anomaly.span_id,
                            "path": path,
                            "status": anomaly.status,
                            "duration_ms": anomaly.duration_ms,
                            "is_error": anomaly.is_error,
                            "is_slow": anomaly.is_slow,
                        },
                    )
                )

        elif task.dimension == "cluster":
            for name in ("cpu_usage", "memory_usage"):
                value = _number(metrics, name)
                if not value:
                    values = _series(metrics, name)
                    value = values[-1] if values else 0.0
                ratio = value / 100 if value > 1 else value
                if ratio >= 0.85:
                    findings.append(_finding(alert, "cluster", f"{name}_high", f"{name} reached {ratio:.1%}", confidence=0.9, severity=EvidenceSeverity.CRITICAL if ratio >= 0.95 else EvidenceSeverity.WARNING, data={"metric": name, "value": ratio}))

        elif task.dimension == "errorlog":
            logs = observed.get("logs.query", {}).get("logs", observed.get("logs.query", {}).get("messages", []))
            text = " ".join(str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in logs)
            if "OutOfMemory" in text or "OOMKilled" in text:
                findings.append(_finding(alert, "errorlog", "oom_signature", "OOM signature observed in error logs", confidence=0.95, severity=EvidenceSeverity.CRITICAL))
            elif logs:
                findings.append(_finding(alert, "errorlog", "error_pattern", f"Collected {len(logs)} error log record(s)", confidence=min(0.5 + len(logs) / 100, 0.8)))

        elif task.dimension == "problem":
            data = observed.get("alerts.query", {})
            related = data.get("related_alerts", data.get("alerts", []))
            known = data.get("known_issues", [])
            if related or known:
                findings.append(_finding(alert, "problem", "related_incident", f"Matched {len(related)} related alert(s) and {len(known)} known issue(s)", confidence=min(0.5 + (len(related) + len(known)) * 0.1, 0.9)))

        output.append(_result(f"{task.dimension}_analyzer", "L1", task.dimension, findings))
    return output


def analyze_experts(alert: AlertEvent, results: list[ToolResult]) -> list[SemanticAnalysisResult]:
    """Run DB/Redis/Kafka/RPC L2 experts over already persisted observations."""
    observed = _observations(results)
    output: list[SemanticAnalysisResult] = []

    db = observed.get("db.inspect", {})
    db_findings: list[DiagnosticFinding] = []
    lag = _number(db, "replication_lag_seconds", _number(db, "slave_delay_seconds"))
    if lag >= 5:
        db_findings.append(_finding(alert, "db", "db_replication_lag", f"DB replication lag is {lag:.1f}s", confidence=0.9, severity=EvidenceSeverity.CRITICAL if lag >= 15 else EvidenceSeverity.WARNING))
    slow = _number(db, "slow_query_count")
    if slow >= 10:
        db_findings.append(_finding(alert, "db", "db_slow_query", f"Slow query count is {slow:.0f}", confidence=0.85))
    active_entry = db.get("active_connections", {})
    active, maximum = _number(db, "active_connections"), _number(db, "max_connections")
    if not maximum and isinstance(active_entry, dict):
        maximum = _number(active_entry, "max")
    maximum = max(maximum, 1)
    if active / maximum >= 0.8:
        db_findings.append(_finding(alert, "db", "db_connection_exhausted", f"DB connection usage is {active / maximum:.1%}", confidence=0.9))
    output.append(_result("db_expert", "L2", "db", db_findings))

    redis = observed.get("redis.inspect", {})
    redis_findings: list[DiagnosticFinding] = []
    memory = _number(redis, "memory_usage_percent")
    if not memory and isinstance(redis.get("used_memory"), dict):
        memory = _number(redis["used_memory"], "usage_ratio") * 100
    if memory >= 80:
        redis_findings.append(_finding(alert, "redis", "redis_memory_pressure", f"Redis memory usage is {memory:.1f}%", confidence=0.9))
    hit_rate = _number(redis, "hit_rate_percent", 100)
    if isinstance(redis.get("hit_rate"), dict):
        hit_rate = _number(redis["hit_rate"], "current", 1) * 100
    if hit_rate <= 90:
        redis_findings.append(_finding(alert, "redis", "redis_low_hit_rate", f"Redis hit rate is {hit_rate:.1f}%", confidence=0.85))
    output.append(_result("redis_expert", "L2", "redis", redis_findings))

    kafka = observed.get("kafka.inspect", {})
    kafka_findings: list[DiagnosticFinding] = []
    lag = _number(kafka, "consumer_lag", _number(kafka, "total_lag"))
    if lag >= 1000:
        kafka_findings.append(_finding(alert, "kafka", "kafka_consumer_lag", f"Kafka consumer lag is {lag:.0f}", confidence=0.9))
    output.append(_result("kafka_expert", "L2", "kafka", kafka_findings))

    rpc = observed.get("rpc.inspect", {})
    rpc_findings: list[DiagnosticFinding] = []
    timeout_rate, error_rate = _number(rpc, "timeout_rate"), _number(rpc, "error_rate")
    latency, baseline = _number(rpc, "latency_ms"), max(_number(rpc, "baseline_latency_ms", 1), 1)
    if timeout_rate >= 0.05 or latency / baseline >= 3:
        rpc_findings.append(_finding(alert, "rpc", "rpc_timeout", f"RPC timeout rate={timeout_rate:.1%}, latency ratio={latency / baseline:.1f}x", confidence=0.9))
    if error_rate >= 0.05:
        rpc_findings.append(_finding(alert, "rpc", "rpc_error_rate", f"RPC error rate is {error_rate:.1%}", confidence=0.85))
    output.append(_result("rpc_expert", "L2", "rpc", rpc_findings))
    return output

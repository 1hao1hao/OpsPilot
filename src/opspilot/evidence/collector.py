"""Convert heterogeneous tool observations into stable evidence facts."""

from __future__ import annotations

import hashlib
from typing import Any

from opspilot.models import (
    AlertEvent,
    Evidence,
    EvidenceSeverity,
    EvidenceSourceType,
    RootCauseType,
    SemanticAnalysisResult,
    ToolResult,
    ToolStatus,
)


def _evidence(
    *,
    alert: AlertEvent,
    source_name: str,
    source_group: str = "",
    evidence_type: str,
    source_type: EvidenceSourceType,
    fact: str,
    severity: EvidenceSeverity,
    confidence: float,
    supports: list[RootCauseType],
    service: str | None = None,
    raw_ref: str | None = None,
) -> Evidence:
    raw = f"{alert.alert_id}|{source_name}|{evidence_type}|{fact}"
    evidence_id = f"ev-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
    return Evidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source_type=source_type,
        source_name=source_name,
        source_group=source_group,
        service=service or alert.service_name,
        observed_at=alert.timestamp,
        fact=fact,
        severity=severity,
        confidence=confidence,
        supports=supports,
        raw_ref=raw_ref,
    )


def _number(data: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = data.get(name, default)
    if isinstance(value, dict):
        value = value.get("current", value.get("value", default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def collect_evidence(alert: AlertEvent, results: list[ToolResult]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for result in results:
        if result.status != ToolStatus.SUCCESS or result.data is None:
            continue
        observations = result.data.get("observations", {})
        converter = _CONVERTERS.get(result.tool_name)
        if converter:
            source_group = f"tool:{result.tool_name}:{result.tool_call_id}"
            evidence.extend(
                item.model_copy(update={"source_group": source_group})
                for item in converter(alert, observations, result.tool_name)
            )
    unique = {item.evidence_id: item for item in evidence}
    return sorted(unique.values(), key=lambda item: (-item.confidence, item.evidence_id))


def collect_semantic_evidence(
    alert: AlertEvent,
    dimension_results: list[SemanticAnalysisResult],
) -> list[Evidence]:
    """Convert L1 semantic findings that cannot be represented by raw aggregates."""
    cause_map = {
        "recent_change": [RootCauseType.BAD_DEPLOYMENT],
        "upstream_error_rate": [RootCauseType.RPC_ERROR_RATE],
        "downstream_span_error": [RootCauseType.RPC_ERROR_RATE],
        "downstream_span_slow": [RootCauseType.RPC_TIMEOUT],
        "cpu_usage_high": [RootCauseType.RESOURCE_SATURATION],
        "memory_usage_high": [RootCauseType.RESOURCE_SATURATION],
        "oom_signature": [RootCauseType.OOM_RESTART],
    }
    evidence: list[Evidence] = []
    for result in dimension_results:
        for finding in result.findings:
            supports = cause_map.get(finding.finding_type, [])
            if finding.dimension == "downstream":
                dependency = f"{finding.service} {finding.data.get('path', '')}".lower()
                if any(token in dependency for token in ("mysql", "postgres", "database", "db")):
                    supports = [
                        RootCauseType.DB_REPLICATION_LAG,
                        RootCauseType.DB_SLOW_QUERY,
                        RootCauseType.DB_CONNECTION_EXHAUSTED,
                    ]
                elif "redis" in dependency:
                    supports = [RootCauseType.REDIS_MEMORY_PRESSURE, RootCauseType.REDIS_LOW_HIT_RATE]
                elif any(token in dependency for token in ("kafka", "broker")):
                    supports = [RootCauseType.KAFKA_CONSUMER_LAG]
            if not supports:
                continue
            raw_ref = None
            if finding.data.get("trace_id") and finding.data.get("span_id"):
                raw_ref = f"trace:{finding.data['trace_id']}/span:{finding.data['span_id']}"
            evidence.append(
                _evidence(
                    alert=alert,
                    source_name=finding.dimension,
                    source_group=(finding.source_groups[0] if finding.source_groups else ""),
                    evidence_type=f"{finding.dimension}.{finding.finding_type}",
                    source_type=EvidenceSourceType.TRACE if finding.dimension == "downstream" else EvidenceSourceType.RULE,
                    fact=finding.summary,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    supports=supports,
                    service=finding.service,
                    raw_ref=raw_ref,
                )
            )
    return evidence


def collect_expert_evidence(
    alert: AlertEvent,
    expert_results: list[SemanticAnalysisResult],
) -> list[Evidence]:
    """Promote selected L2 Expert findings into the shared Evidence Pool."""
    cause_map = {
        "db_replication_lag": RootCauseType.DB_REPLICATION_LAG,
        "db_slow_query": RootCauseType.DB_SLOW_QUERY,
        "db_connection_exhausted": RootCauseType.DB_CONNECTION_EXHAUSTED,
        "redis_memory_pressure": RootCauseType.REDIS_MEMORY_PRESSURE,
        "redis_low_hit_rate": RootCauseType.REDIS_LOW_HIT_RATE,
        "kafka_consumer_lag": RootCauseType.KAFKA_CONSUMER_LAG,
        "rpc_timeout": RootCauseType.RPC_TIMEOUT,
        "rpc_error_rate": RootCauseType.RPC_ERROR_RATE,
    }
    evidence: list[Evidence] = []
    for result in expert_results:
        for finding in result.findings:
            cause = cause_map.get(finding.finding_type)
            if cause is None:
                continue
            evidence.append(
                _evidence(
                    alert=alert,
                    source_name=result.name,
                    source_group=(finding.source_groups[0] if finding.source_groups else ""),
                    evidence_type=f"expert.{finding.finding_type}",
                    source_type=EvidenceSourceType.RULE,
                    fact=finding.summary,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    supports=[cause],
                    service=finding.service,
                )
            )
    return evidence


def _db(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    lag = _number(data, "replication_lag_seconds", _number(data, "slave_delay_seconds"))
    slow = _number(data, "slow_query_count")
    active_entry = data.get("active_connections", {})
    active = _number(data, "active_connections")
    maximum = _number(data, "max_connections")
    if not maximum and isinstance(active_entry, dict):
        maximum = _number(active_entry, "max")
    maximum = max(maximum, 1)
    if lag >= 5:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="db.replication_lag", source_type=EvidenceSourceType.METRIC, fact=f"DB replication lag is {lag:.1f}s", severity=EvidenceSeverity.CRITICAL if lag >= 15 else EvidenceSeverity.WARNING, confidence=min(0.65 + lag / 100, 0.95), supports=[RootCauseType.DB_REPLICATION_LAG]))
    if slow >= 10:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="db.slow_query", source_type=EvidenceSourceType.METRIC, fact=f"Slow query count is {slow:.0f}", severity=EvidenceSeverity.CRITICAL if slow >= 50 else EvidenceSeverity.WARNING, confidence=min(0.65 + slow / 200, 0.95), supports=[RootCauseType.DB_SLOW_QUERY]))
    if active / maximum >= 0.8:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="db.connection_usage", source_type=EvidenceSourceType.METRIC, fact=f"DB connection usage is {active / maximum:.1%}", severity=EvidenceSeverity.CRITICAL if active / maximum >= 0.95 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.DB_CONNECTION_EXHAUSTED]))
    return items


def _redis(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    memory = _number(data, "memory_usage_percent")
    if not memory and isinstance(data.get("used_memory"), dict):
        memory = _number(data["used_memory"], "usage_ratio") * 100
    hit_rate = _number(data, "hit_rate_percent", 100)
    if "hit_rate" in data and isinstance(data["hit_rate"], dict):
        hit_rate = _number(data["hit_rate"], "current", 1) * 100
    if memory >= 80:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="redis.memory_usage", source_type=EvidenceSourceType.METRIC, fact=f"Redis memory usage is {memory:.1f}%", severity=EvidenceSeverity.CRITICAL if memory >= 95 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.REDIS_MEMORY_PRESSURE]))
    if hit_rate <= 90:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="redis.hit_rate", source_type=EvidenceSourceType.METRIC, fact=f"Redis hit rate is {hit_rate:.1f}%", severity=EvidenceSeverity.CRITICAL if hit_rate < 70 else EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.REDIS_LOW_HIT_RATE, RootCauseType.REDIS_MEMORY_PRESSURE]))
    return items


def _kafka(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    lag = _number(data, "consumer_lag", _number(data, "total_lag"))
    if lag < 1000:
        return []
    return [_evidence(alert=alert, source_name=source, evidence_type="kafka.consumer_lag", source_type=EvidenceSourceType.METRIC, fact=f"Kafka consumer lag is {lag:.0f}", severity=EvidenceSeverity.CRITICAL if lag >= 10000 else EvidenceSeverity.WARNING, confidence=min(0.7 + lag / 100000, 0.95), supports=[RootCauseType.KAFKA_CONSUMER_LAG])]


def _rpc(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    timeout_rate = _number(data, "timeout_rate")
    error_rate = _number(data, "error_rate")
    latency = _number(data, "latency_ms")
    baseline = max(_number(data, "baseline_latency_ms", 1), 1)
    if timeout_rate >= 0.05 or latency / baseline >= 3:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="rpc.timeout", source_type=EvidenceSourceType.TRACE, fact=f"RPC timeout rate={timeout_rate:.1%}, latency ratio={latency / baseline:.1f}x", severity=EvidenceSeverity.CRITICAL if timeout_rate >= 0.1 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.RPC_TIMEOUT]))
    if error_rate >= 0.05:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="rpc.error_rate", source_type=EvidenceSourceType.TRACE, fact=f"RPC error rate is {error_rate:.1%}", severity=EvidenceSeverity.CRITICAL if error_rate >= 0.1 else EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.RPC_ERROR_RATE]))
    return items


def _metrics(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    cpu = _number(data, "cpu_usage")
    memory = _number(data, "memory_usage")
    if cpu >= 0.85 or memory >= 0.85:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="metric.resource_saturation", source_type=EvidenceSourceType.METRIC, fact=f"Resource usage cpu={cpu:.1%}, memory={memory:.1%}", severity=EvidenceSeverity.CRITICAL if max(cpu, memory) >= 0.95 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.RESOURCE_SATURATION]))
    return items


def _logs(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    messages = data.get("messages", data.get("logs", []))
    text = " ".join(str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in messages)
    if "OutOfMemory" not in text and "OOMKilled" not in text:
        return []
    return [_evidence(alert=alert, source_name=source, evidence_type="log.oom", source_type=EvidenceSourceType.LOG, fact="OOM signature observed in service logs", severity=EvidenceSeverity.CRITICAL, confidence=0.95, supports=[RootCauseType.OOM_RESTART])]


def _changes(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    count = _number(data, "high_risk_count")
    recent = data.get("recent_deployment", False)
    if not recent and count < 1:
        return []
    return [_evidence(alert=alert, source_name=source, evidence_type="change.recent_deployment", source_type=EvidenceSourceType.CHANGE, fact="A recent high-risk deployment overlaps the incident window", severity=EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.BAD_DEPLOYMENT])]


def _empty(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    return []


_CONVERTERS = {
    "metrics.query": _metrics,
    "logs.query": _logs,
    "changes.query": _changes,
    "traces.query": _rpc,
    "topology.query": _empty,
    "db.inspect": _db,
    "redis.inspect": _redis,
    "kafka.inspect": _kafka,
    "rpc.inspect": _rpc,
    "db.replication": _db,
    "db.slowlog": _db,
    "db.connections": _db,
    "redis.memory": _redis,
    "redis.hotkeys": _redis,
    "kafka.lag": _kafka,
    "rpc.metrics": _rpc,
}

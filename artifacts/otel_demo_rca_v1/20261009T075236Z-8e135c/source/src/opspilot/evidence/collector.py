"""Convert heterogeneous tool observations into stable evidence facts."""

from __future__ import annotations

import hashlib
from typing import Any

from opspilot.evidence.span_cohorts import DATABASE_SYSTEMS, database_latency_cohorts
from opspilot.evidence.temporal import change
from opspilot.models import (
    AlertEvent,
    Evidence,
    EvidenceSeverity,
    EvidenceSourceType,
    RootCauseType,
    ToolResult,
    ToolStatus,
)
from opspilot.tracing import detect_span_anomalies


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
    raw = f"{alert.alert_id}|{source_name}|{service or alert.service_name}|{evidence_type}|{fact}"
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
        points = value.get("data_points", [])
        last = points[-1] if points else default
        last = last.get("value", default) if isinstance(last, dict) else last
        value = value.get("current", value.get("value", value.get("aggregation", {}).get("current", last)))
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
                item.model_copy(update={"source_group": source_group, "raw_ref": item.raw_ref or result.tool_call_id})
                for item in converter(alert, observations, result.tool_name)
            )
    unique = {item.evidence_id: item for item in evidence}
    return sorted(unique.values(), key=lambda item: (-item.confidence, item.evidence_id))


def _db(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    lag = _number(data, "replication_lag_seconds", _number(data, "slave_delay_seconds"))
    slow = _number(data, "slow_query_count")
    if "database_latency_cohorts" in data:
        slow = sum(c["slow_count"] for c in data["database_latency_cohorts"] if c["corroborated"])
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
    hit_payload = data.get("hit_rate_percent", data.get("hit_rate"))
    has_context = isinstance(hit_payload, dict) and hit_payload.get("temporal_context")
    hit_change = change(hit_payload, direction="down", minimum_delta=10, minimum_ratio=1.2)
    if memory >= 80:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="redis.memory_usage", source_type=EvidenceSourceType.METRIC, fact=f"Redis memory usage is {memory:.1f}%", severity=EvidenceSeverity.CRITICAL if memory >= 95 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.REDIS_MEMORY_PRESSURE]))
    if hit_rate <= 90 and (not has_context or hit_change):
        items.append(_evidence(alert=alert, source_name=source, evidence_type="redis.hit_rate", source_type=EvidenceSourceType.METRIC, fact=f"Redis hit rate is {hit_rate:.1f}%", severity=EvidenceSeverity.CRITICAL if hit_rate < 70 else EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.REDIS_LOW_HIT_RATE]))
    return items


def _kafka(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    lag = _number(data, "consumer_lag", _number(data, "total_lag"))
    payload = data.get("consumer_lag", data.get("total_lag"))
    shift = change(payload, minimum_delta=100, minimum_ratio=2)
    context = isinstance(payload, dict) and payload.get("temporal_context")
    if not shift and (context or lag < 1000):
        return []
    fact = f"Kafka consumer lag is {lag:.0f}" + (f"; temporal change={shift}" if shift else "")
    return [_evidence(alert=alert, source_name=source, evidence_type="kafka.consumer_lag", source_type=EvidenceSourceType.METRIC, fact=fact, severity=EvidenceSeverity.CRITICAL if lag >= 10000 else EvidenceSeverity.WARNING, confidence=0.9 if shift else min(0.7 + lag / 100000, 0.95), supports=[RootCauseType.KAFKA_CONSUMER_LAG])]


def _rpc(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    timeout_rate = _number(data, "timeout_rate")
    error_rate = _number(data, "error_rate")
    latency = _number(data, "latency_ms")
    baseline = max(_number(data, "baseline_latency_ms"), 1)
    latency_shift = "baseline_latency_ms" in data and latency / baseline >= 3
    if _number(data, "call_volume") > 0:
        # An absence of positive evidence does not tell the planner whether
        # calls were measured and normal or their outcomes were unavailable.
        observed = {name: data[name] for name in ("call_volume", "timeout_rate", "error_rate",
                                                 "latency_ms", "baseline_latency_ms") if name in data}
        items.append(_evidence(alert=alert, source_name=source, evidence_type="rpc.observed_outcomes",
                               source_type=EvidenceSourceType.TRACE,
                               fact=f"Observed outgoing RPC samples: {observed}; missing outcomes are unknown. Rates describe sampled calls, not every dependency.",
                               severity=EvidenceSeverity.INFO, confidence=0.5, supports=[]))
    if timeout_rate >= 0.05 or (latency_shift and "timeout_rate" not in data):
        items.append(_evidence(alert=alert, source_name=source, evidence_type="rpc.timeout", source_type=EvidenceSourceType.TRACE, fact=f"RPC timeout rate={timeout_rate:.1%}, latency ratio={latency / baseline:.1f}x", severity=EvidenceSeverity.CRITICAL if timeout_rate >= 0.1 else EvidenceSeverity.WARNING, confidence=0.9, supports=[RootCauseType.RPC_TIMEOUT]))
    if error_rate >= 0.05:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="rpc.error_rate", source_type=EvidenceSourceType.TRACE, fact=f"RPC error rate is {error_rate:.1%}", severity=EvidenceSeverity.CRITICAL if error_rate >= 0.1 else EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.RPC_ERROR_RATE]))
    return items


def _metrics(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items: list[Evidence] = []
    cpu = _number(data, "cpu_usage")
    memory = _number(data, "memory_usage")
    cpu = cpu / 100 if cpu > 1 else cpu
    memory = memory / 100 if memory > 1 else memory
    for name in ("cpu_usage", "memory_usage", "disk_usage"):
        payload = data.get(name)
        shift = change(payload, minimum_delta=0.05, minimum_ratio=1.1, reset_aware=True)
        if shift:
            items.append(_evidence(alert=alert, source_name=source, evidence_type="metric.resource_growth",
                                   source_type=EvidenceSourceType.METRIC,
                                   fact=f"{name}: sustained resource increase {shift}",
                                   severity=EvidenceSeverity.WARNING, confidence=0.85,
                                   supports=[RootCauseType.RESOURCE_SATURATION]))
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
    if not recent and count < 1 and not data.get("changes"):
        return []
    return [_evidence(alert=alert, source_name=source, evidence_type="change.recent_deployment", source_type=EvidenceSourceType.CHANGE, fact="A recent high-risk deployment overlaps the incident window", severity=EvidenceSeverity.WARNING, confidence=0.85, supports=[RootCauseType.BAD_DEPLOYMENT])]


def _traces(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    items = _rpc(alert, data, source)
    raw_spans = {(t.get("trace_id"), s.get("span_id")): s
                 for t in data.get("traces", []) for s in t.get("spans", [])}
    database_support, _ = database_latency_cohorts(raw_spans, alert.timestamp.timestamp())
    producers = {s.get("operation", "") for s in raw_spans.values() if s.get("span_kind") in {"producer", "consumer"}}
    if producers:
        items.append(_evidence(alert=alert, source_name=source, evidence_type="trace.messaging_dependency",
                               source_type=EvidenceSourceType.TRACE,
                               fact=f"Observed messaging producer/consumer operations: {sorted(producers)}; this is a diagnostic dependency hint, not fault evidence",
                               severity=EvidenceSeverity.INFO, confidence=0.3, supports=[]))
    for anomaly in detect_span_anomalies(data):
        path = "/".join(anomaly.path)
        raw = raw_spans.get((anomaly.trace_id, anomaly.span_id), {})
        dependency = f"{anomaly.service} {anomaly.operation} {path}".lower()
        supports = [RootCauseType.RPC_ERROR_RATE] if anomaly.is_error else []
        if anomaly.status == "TIMEOUT" or raw.get("is_timeout"):
            supports = [RootCauseType.RPC_TIMEOUT]
        database = raw.get("db_system") in DATABASE_SYSTEMS
        corroborated = (anomaly.trace_id, anomaly.span_id) in database_support
        if database and anomaly.is_slow:
            supports = [RootCauseType.DB_SLOW_QUERY] if corroborated else []
        elif any(token in dependency for token in ("mysql", "postgres", "database", "db")):
            # An untyped database dependency failure warrants drill-down; it
            # cannot distinguish replication, query or connection mechanisms.
            supports = [RootCauseType.DB_REPLICATION_LAG, RootCauseType.DB_SLOW_QUERY,
                        RootCauseType.DB_CONNECTION_EXHAUSTED]
        elif "redis" in dependency:
            supports = [RootCauseType.REDIS_MEMORY_PRESSURE, RootCauseType.REDIS_LOW_HIT_RATE]
        elif any(token in dependency for token in ("kafka", "broker")):
            supports = [RootCauseType.KAFKA_CONSUMER_LAG]
        items.append(_evidence(
            alert=alert, source_name=source,
            evidence_type=("trace.db_slow" if corroborated else "trace.db_latency_hint") if database and anomaly.is_slow else "trace.span_error" if anomaly.is_error else "trace.span_slow",
            source_type=EvidenceSourceType.TRACE,
            fact=f"Trace {anomaly.trace_id} path {path}: status={anomaly.status}, duration={anomaly.duration_ms:.1f}ms",
            severity=EvidenceSeverity.CRITICAL if anomaly.is_error else EvidenceSeverity.WARNING,
            confidence=0.9, supports=supports, service=anomaly.service,
            raw_ref=f"trace:{anomaly.trace_id}/span:{anomaly.span_id}",
        ))
    return items


def _empty(alert: AlertEvent, data: dict[str, Any], source: str) -> list[Evidence]:
    return []


_CONVERTERS = {
    "metrics.query": _metrics,
    "logs.query": _logs,
    "changes.query": _changes,
    "traces.query": _traces,
    "topology.query": _empty,
    "db.replication": _db,
    "db.slowlog": _db,
    "db.connections": _db,
    "redis.memory": _redis,
    "redis.hotkeys": _redis,
    "kafka.lag": _kafka,
    "rpc.metrics": _rpc,
}

"""Deterministic L3 pipeline executed between Evidence collection and ranking."""

from __future__ import annotations

import hashlib
from typing import Any

from opspilot.models import (
    AlertEvent,
    AlgorithmSignal,
    Evidence,
    EvidenceSeverity,
    EvidenceSourceType,
    RootCauseType,
    SemanticAnalysisResult,
    ToolResult,
    ToolStatus,
)
from opspilot.rca import (
    ExpertRuleEngine,
    MetricFilter,
    MultiDimensionComparator,
    NoiseFilter,
    QuantileAnomalyDetector,
    VolatilityDetector,
)


def _observations(results: list[ToolResult], tool_name: str) -> dict[str, Any]:
    for result in results:
        if result.tool_name == tool_name and result.status == ToolStatus.SUCCESS and result.data:
            return result.data.get("observations", {})
    return {}


def _tool_source_group(results: list[ToolResult], tool_name: str) -> str:
    for result in results:
        if result.tool_name == tool_name and result.status == ToolStatus.SUCCESS:
            return f"tool:{tool_name}:{result.tool_call_id}"
    return ""


def _metric_view(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    known = ("qps", "error_rate", "tp99", "tp95", "cpu_usage", "memory_usage", "disk_usage")
    metrics: dict[str, dict[str, Any]] = {}
    for name in known:
        value = raw.get(name)
        if isinstance(value, dict):
            payload = dict(value)
        elif isinstance(value, (int, float)):
            payload = {"current": float(value), "value": float(value)}
        elif raw.get("metric") == name:
            payload = dict(raw)
        else:
            continue
        points = payload.get("data_points", [])
        series = [float(point.get("value", 0)) for point in points if isinstance(point, dict)]
        aggregation = payload.get("aggregation", {})
        if "current" not in payload and aggregation.get("current") is not None:
            payload["current"] = aggregation["current"]
        if "current" not in payload and series:
            payload["current"] = series[-1]
        payload.setdefault("baseline_series", series[:-1])
        payload.setdefault("time_series", series)
        metrics[name] = payload
    return metrics


def _signal(
    algorithm: str,
    metric: str,
    signal_type: str,
    is_anomaly: bool,
    confidence: float,
    details: dict[str, Any],
    source_group: str = "",
) -> AlgorithmSignal:
    return AlgorithmSignal(
        algorithm=algorithm,
        metric=metric,
        signal_type=signal_type,
        is_anomaly=is_anomaly,
        confidence=max(0.0, min(float(confidence), 1.0)),
        details=details,
        source_group=source_group,
    )


def _supports_for_metric(metric: str) -> list[RootCauseType]:
    if metric in {"cpu_usage", "memory_usage", "disk_usage", "qps"}:
        return [RootCauseType.RESOURCE_SATURATION]
    if metric == "error_rate":
        return [RootCauseType.RPC_ERROR_RATE]
    if metric in {"tp99", "tp95"}:
        return [RootCauseType.RPC_TIMEOUT]
    return []


def _algorithm_evidence(alert: AlertEvent, item: AlgorithmSignal) -> Evidence | None:
    supports = _supports_for_metric(item.metric)
    if not item.is_anomaly or not supports:
        return None
    fact = f"{item.algorithm} detected {item.signal_type} on {item.metric}"
    raw = f"{alert.alert_id}|{item.algorithm}|{item.metric}|{item.signal_type}|{item.details}"
    return Evidence(
        evidence_id=f"ev-{hashlib.sha256(raw.encode()).hexdigest()[:16]}",
        evidence_type=f"algorithm.{item.signal_type}",
        source_type=EvidenceSourceType.RULE,
        source_name=item.algorithm,
        source_group=item.source_group,
        service=alert.service_name,
        observed_at=alert.timestamp,
        fact=fact,
        severity=EvidenceSeverity.WARNING,
        confidence=item.confidence,
        supports=supports,
    )


def _legacy_results(items: list[SemanticAnalysisResult]) -> list[dict[str, Any]]:
    return [
        {
            "agent_name": item.name,
            "dimension": item.dimension,
            "confidence": item.confidence,
            "findings": [
                {**finding.data, "type": finding.finding_type, "desc": finding.summary}
                for finding in item.findings
            ],
        }
        for item in items
    ]


_RULE_CAUSES = {
    "R001": RootCauseType.BAD_DEPLOYMENT,
    "R002": RootCauseType.DB_REPLICATION_LAG,
    "R003": RootCauseType.OOM_RESTART,
    "R004": RootCauseType.KAFKA_CONSUMER_LAG,
    "R005": RootCauseType.RESOURCE_SATURATION,
    "R006": RootCauseType.RPC_TIMEOUT,
    "R007": RootCauseType.DB_CONNECTION_EXHAUSTED,
}


def _rule_evidence(alert: AlertEvent, matched: dict[str, Any], base_evidence: list[Evidence]) -> Evidence | None:
    rule_id = str(matched.get("rule_id", ""))
    cause = _RULE_CAUSES.get(rule_id)
    if cause is None:
        return None
    confidence = float(matched.get("confidence", 0.7 + float(matched.get("boost", 0))))
    fact = f"Expert rule {rule_id} ({matched.get('name', '')}) matched the collected evidence"
    raw = f"{alert.alert_id}|{rule_id}|{cause.value}"
    source_group = next(
        (
            item.source_group
            for item in base_evidence
            if cause in item.supports and item.source_group
        ),
        "",
    )
    return Evidence(
        evidence_id=f"ev-{hashlib.sha256(raw.encode()).hexdigest()[:16]}",
        evidence_type=f"rule.{rule_id.lower()}",
        source_type=EvidenceSourceType.RULE,
        source_name="expert_rule_engine",
        source_group=source_group,
        service=alert.service_name,
        observed_at=alert.timestamp,
        fact=fact,
        severity=EvidenceSeverity.CRITICAL if confidence >= 0.9 else EvidenceSeverity.WARNING,
        confidence=min(confidence, 0.95),
        supports=[cause],
    )


def run_deterministic_pipeline(
    alert: AlertEvent,
    tool_results: list[ToolResult],
    dimension_results: list[SemanticAnalysisResult],
    expert_results: list[SemanticAnalysisResult],
    base_evidence: list[Evidence],
) -> tuple[list[AlgorithmSignal], list[Evidence], list[str]]:
    """Run filter -> noise -> WoW/DoD -> IQR/volatility -> expert rules."""
    metrics = _metric_view(_observations(tool_results, "metrics.query"))
    metric_source_group = _tool_source_group(tool_results, "metrics.query")
    signals: list[AlgorithmSignal] = []

    metric_candidates = MetricFilter().filter(metrics)
    retained = NoiseFilter().filter_noise(metric_candidates)
    retained_keys = {(item.get("metric"), item.get("dimension")) for item in retained}
    for candidate in metric_candidates:
        kept = (candidate.get("metric"), candidate.get("dimension")) in retained_keys
        signals.append(_signal("metric_filter+noise_filter", str(candidate.get("metric", "")), str(candidate.get("dimension", "metric_anomaly") if kept else "noise_filtered"), kept, candidate.get("confidence", 0), candidate, metric_source_group))

    comparator = MultiDimensionComparator()
    quantile = QuantileAnomalyDetector()
    volatility = VolatilityDetector()
    for metric, payload in metrics.items():
        current = payload.get("current")
        if current is not None and payload.get("last_week") is not None:
            compared = comparator.compare(float(current), {"last_week": payload.get("last_week"), "yesterday": payload.get("yesterday")})
            signals.append(_signal("wow_dod_comparator", metric, "baseline_shift", bool(compared["is_anomaly"]), 0.85 if compared["is_anomaly"] else 0.3, compared, metric_source_group))
        baseline = payload.get("baseline_series", [])
        if current is not None and baseline:
            detected = quantile.detect([float(value) for value in baseline], float(current))
            signals.append(_signal("iqr_detector", metric, detected.anomaly_type, detected.is_anomaly, detected.confidence, {"baseline": detected.baseline_value, "current": detected.current_value, "deviation_ratio": detected.deviation_ratio, **detected.details}, metric_source_group))
        series = payload.get("time_series", [])
        if series:
            detected = volatility.detect_volatility_change([float(value) for value in series])
            changed = bool(detected.get("has_volatility_change", False))
            signals.append(_signal("volatility_detector", metric, "volatility_change", changed, 0.8 if changed else 0.3, detected, metric_source_group))

    evidence_summary = {
        "top_evidences": [
            {"finding": item.fact, "dimension": item.source_type.value, "confidence": item.confidence}
            for item in base_evidence
        ]
    }
    semantic = dimension_results + expert_results
    anomalies = [
        {"metric": item.metric, "type": item.signal_type, "confidence": item.confidence}
        for item in signals
        if item.is_anomaly
    ]
    alert_context = alert.model_dump(mode="json")
    alert_context["metrics"] = metrics
    matched = ExpertRuleEngine().evaluate(evidence_summary, _legacy_results(semantic), alert_context, anomalies)
    matched_ids = [str(item["rule_id"]) for item in matched]

    derived = [item for signal in signals if (item := _algorithm_evidence(alert, signal)) is not None]
    derived.extend(item for rule in matched if (item := _rule_evidence(alert, rule, base_evidence)) is not None)
    return signals, derived, matched_ids

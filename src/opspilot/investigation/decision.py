"""Deterministic, scope-aware fault existence and diagnostic sufficiency.

Ranking and execution success are inputs, never substitutes for observation
coverage. No control metadata, expected answers or LLM decisions are read here.
"""

import math
import re
from collections import defaultdict
from datetime import datetime
from statistics import median

from opspilot.evidence.span_cohorts import database_latency_cohorts
from opspilot.evidence.temporal import change
from opspilot.models import (
    DiagnosisDecision,
    DiagnosisVerdict,
    EvidenceRequirement,
    FaultPresence,
    ObservationAssessment,
    ObservationQuality,
    RootCauseType,
    ToolStatus,
)

SIGNAL_TOOLS = {
    "latency": ["metrics.query", "traces.query", "rpc.metrics"],
    "error": ["metrics.query", "traces.query", "rpc.metrics"],
    "timeout": ["traces.query", "rpc.metrics"],
    "cpu_usage": ["metrics.query"], "memory_usage": ["metrics.query", "logs.query"],
    "disk_usage": ["metrics.query"], "activity": ["metrics.query", "traces.query", "logs.query"],
    "db_slow_query": ["db.slowlog"], "db_replication_lag": ["db.replication"],
    "db_connection_exhausted": ["db.connections"], "redis_memory_pressure": ["redis.memory"],
    "redis_low_hit_rate": ["redis.hotkeys"], "kafka_consumer_lag": ["kafka.lag"],
    "bad_deployment": ["changes.query"], "oom_restart": ["logs.query"],
    "completion": ["traces.query", "logs.query"],
}
CAUSE_SIGNALS = {"rpc_timeout": "timeout", "rpc_error_rate": "error",
                 "oom_restart": "memory_usage"}
METRIC_SIGNALS = {"tp95": "latency", "tp99": "latency", "error_rate": "error",
                  "cpu_usage": "cpu_usage", "memory_usage": "memory_usage", "disk_usage": "disk_usage"}


def number(value):
    if isinstance(value, dict):
        value = value.get("current", value.get("value"))
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def incident_local(value, alert):
    stamp = number(value)
    if stamp is None and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            stamp = parsed.timestamp() if parsed.tzinfo else None
        except ValueError:
            pass
    return stamp is not None and abs(stamp - alert.timestamp.timestamp()) <= 60


def alert_threshold(alert, signal):
    if alert.labels.get("operator", ">=") not in {">", ">="}:
        return None
    metric = alert.labels.get("metric", "")
    if METRIC_SIGNALS.get(metric, metric) != signal:
        return None
    value = number(alert.labels.get("threshold"))
    if value is None or value < 0:
        return None
    unit = alert.labels.get("unit", "").lower()
    if signal.endswith("_usage") and unit in {"percent", "%"}:
        value /= 100
    elif signal == "error" and unit in {"ratio", "fraction"}:
        value *= 100
    elif signal == "latency" and unit in {"seconds", "s"}:
        value *= 1000
    elif unit in {"bytes", "cores"}:
        return None  # The normalized gauge cannot be compared to raw capacity.
    return value


def reference(payload, signal, alert):
    """A finite stable reference below a conservative health envelope.

    A single scalar baseline or a query succeeding does not establish health.
    These envelopes only constrain an ABSENT conclusion, never diagnose a fault.
    """
    if not isinstance(payload, dict):
        return False
    history = payload.get("reference_series", payload.get("baseline_series", []))
    recent = payload.get("recent_series", [])
    if not isinstance(history, list) or not isinstance(recent, list):
        return False
    values = [number(v) for v in history]
    current = [number(v) for v in recent]
    if len(values) < 3 or len(current) < 3 or any(v is None or v < 0 for v in values + current):
        return False
    ceiling = 1000 if signal == "latency" else 5 if signal == "error" else 0.85
    explicit = alert_threshold(alert, signal)
    if explicit is not None:
        ceiling = explicit
    base = median(values)
    noise = median(abs(v - base) for v in values)
    return max(values + current) < ceiling and noise <= max(ceiling * 0.02, abs(base) * 0.1)


def linked_services(alert, results):
    services = {alert.service_name}
    for result in results:
        for trace in ((result.data or {}).get("observations", {})).get("traces", []):
            spans = {s.get("span_id"): s for s in trace.get("spans", []) if s.get("span_id")}
            active = {key for key, s in spans.items() if s.get("service") == alert.service_name
                      and incident_local(s.get("start_time"), alert)}
            while True:
                children = {key for key, s in spans.items() if s.get("parent_span_id") in active}
                expanded = active | children
                if expanded == active:
                    break
                active = expanded
            services.update(spans[key].get("service") for key in active
                            if incident_local(spans[key].get("start_time"), alert))
    return services


def initial_signals(alert, results):
    kind = alert.alert_type.value
    if kind == "timeout":
        return {"latency", "timeout", "activity"}
    if kind == "error_rate":
        return {"error", "activity"}
    if kind == "resource":
        words = set(re.findall(r"[a-z_]+", alert.description.lower() + " " + alert.labels.get("metric", "")))
        selected = {signal for token, signal in (("cpu", "cpu_usage"), ("memory", "memory_usage"),
                                                 ("disk", "disk_usage")) if token in words or signal in words}
        if not selected:
            selected = {"cpu_usage", "memory_usage"}
            if any("disk_usage" in (r.data or {}).get("observations", {}) for r in results):
                selected.add("disk_usage")
        return selected | {"activity"}
    signals = {"latency", "error", "cpu_usage", "memory_usage", "activity"}
    if any(word in alert.description.lower() for word in ("completion", "backlog", "queue")):
        # Request latency is not a measurement of asynchronous completion.
        # With no captured completion observation, this remains unmet.
        signals.add("completion")
    return signals


def assess_observations(alert, results, evidence):
    scope = linked_services(alert, results)
    findings = []

    def add(result, signal, quality, reason, *, healthy=False, ids=None, service=None):
        findings.append(ObservationAssessment(
            observation_id=f"{result.tool_call_id}:{signal}:{len(findings)}", tool_name=result.tool_name,
            signal=signal, service=service or alert.service_name, quality=quality,
            reason=reason, healthy_reference=healthy, evidence_ids=ids or []))

    for result in results:
        potential = [s for s, tools in SIGNAL_TOOLS.items() if result.tool_name in tools]
        if result.status != ToolStatus.SUCCESS:
            for signal in potential:
                add(result, signal, ObservationQuality.FAILED, result.error_code or "tool failed")
            continue
        data = (result.data or {}).get("observations", {})
        if not data:
            for signal in potential:
                add(result, signal, ObservationQuality.NO_DATA, "successful call returned no observations")
            continue
        declared = data.get("service_name", data.get("service"))
        if declared and declared not in scope:
            for signal in potential:
                add(result, signal, ObservationQuality.UNSCOPED, "observation belongs to another service")
            continue
        source_evidence = [e for e in evidence if e.source_group == f"tool:{result.tool_name}:{result.tool_call_id}"]
        trace_spans = {(t.get("trace_id"), s.get("span_id")): s
                       for t in data.get("traces", []) for s in t.get("spans", [])
                       if s.get("service") in scope and incident_local(s.get("start_time"), alert)}
        _, database_cohorts = database_latency_cohorts(trace_spans, alert.timestamp.timestamp())
        if result.tool_name == "metrics.query":
            for metric, signal in METRIC_SIGNALS.items():
                if metric not in data:
                    continue
                payload = data[metric]
                value = number(payload)
                points = payload.get("data_points", []) if isinstance(payload, dict) else []
                if points and any("timestamp" in p for p in points) and not incident_local(points[-1].get("timestamp"), alert):
                    add(result, signal, ObservationQuality.NO_DATA, "current metric is outside the incident window")
                    continue
                if value is None or value < 0:
                    add(result, signal, ObservationQuality.NO_DATA, "non-finite or missing current metric")
                    continue
                ids = [e.evidence_id for e in source_evidence if e.service in scope and e.supports
                       and (e.fact.startswith(metric + ":") or
                            e.evidence_type == "metric.resource_saturation" and signal in {"cpu_usage", "memory_usage"}
                            and value >= 0.85)]
                absolute_resource = signal.endswith("_usage") and value >= 0.85
                threshold = alert_threshold(alert, signal)
                explicit_anomaly = threshold is not None and (
                    value > threshold if alert.labels.get("operator") == ">" else value >= threshold)
                if explicit_anomaly or threshold is None and (ids or absolute_resource):
                    add(result, signal, ObservationQuality.ABNORMAL, "material scoped metric anomaly", ids=ids)
                elif reference(payload, signal, alert):
                    # Recheck the current window; health is not inferred from
                    # the absence of ranker votes or from successful collection.
                    shift = change(payload, minimum_delta=100 if signal == "latency" else 1 if signal == "error" else 0.05,
                                   minimum_ratio=3 if signal == "latency" else 1.5 if signal == "error" else 1.1,
                                   reset_aware=signal.endswith("_usage"))
                    if shift and threshold is None:
                        add(result, signal, ObservationQuality.ABNORMAL, f"observed material change {shift}")
                    else:
                        add(result, signal, ObservationQuality.NORMAL, "stable recent metric with a valid healthy reference", healthy=True)
                else:
                    add(result, signal, ObservationQuality.MISSING_BASELINE, "missing, unstable, insufficient or unhealthy reference")
            qps = number(data.get("qps"))
            if qps is not None and qps > 0:
                add(result, "activity", ObservationQuality.NORMAL, "positive scoped request throughput", healthy=True)
        if result.tool_name in {"rpc.metrics", "traces.query"}:
            count = number(data.get("call_volume"))
            if count is not None and count > 0:
                add(result, "activity", ObservationQuality.NORMAL, f"observed {count:g} outgoing calls", healthy=True)
                for field, signal in (("timeout_rate", "timeout"), ("error_rate", "error")):
                    rate = number(data.get(field))
                    if rate is None or not 0 <= rate <= 1:
                        add(result, signal, ObservationQuality.NO_DATA, "protocol outcome unavailable")
                    elif rate >= 0.05:
                        ids = [e.evidence_id for e in source_evidence if e.supports and e.service in scope]
                        add(result, signal, ObservationQuality.ABNORMAL, f"observed {field}={rate:g} over {count:g} calls", ids=ids)
                    elif count >= 3:
                        add(result, signal, ObservationQuality.NORMAL, f"measured {field}={rate:g}; outgoing protocol outcomes only",
                            healthy="downstream" in alert.description.lower() or "rpc" in alert.description.lower())
                    else:
                        add(result, signal, ObservationQuality.MISSING_BASELINE, "too few normal protocol samples")
            if result.tool_name == "traces.query":
                scoped = []
                for trace in data.get("traces", []):
                    for span in trace.get("spans", []):
                        if span.get("service") not in scope:
                            continue
                        started = number(span.get("start_time"))
                        if started is None or not alert.timestamp.timestamp() - 60 <= started <= alert.timestamp.timestamp() + 60:
                            continue
                        scoped.append(span)
                        status = span.get("status", "").upper()
                        if status in {"ERROR", "FAILED", "TIMEOUT", "CANCELLED", "ABORTED"}:
                            ids = [e.evidence_id for e in source_evidence if e.raw_ref == f"trace:{trace.get('trace_id')}/span:{span.get('span_id')}"]
                            add(result, "timeout" if status == "TIMEOUT" else "error", ObservationQuality.ABNORMAL,
                                f"incident-local failed span {span.get('span_id')}", ids=ids, service=span["service"])
                if scoped:
                    add(result, "activity", ObservationQuality.NORMAL, f"{len(scoped)} incident-local scoped spans", healthy=True)
                if len(scoped) >= 3 and all(s.get("status", "").upper() == "OK" for s in scoped):
                    target = [s for s in scoped if s.get("service") == alert.service_name]
                    add(result, "error", ObservationQuality.NORMAL, "explicitly successful scoped spans", healthy=len(target) >= 3)
                    durations = [number(s.get("duration_ms")) for s in target]
                    add(result, "timeout", ObservationQuality.NORMAL, "successful calls with observed durations",
                        healthy=len(target) >= 3 and all(d is not None and 0 <= d < 1000 for d in durations))
        measurements = {
            "kafka.lag": ("kafka_consumer_lag", "consumer_lag", 1000),
            "db.replication": ("db_replication_lag", "replication_lag_seconds", 5),
            "redis.memory": ("redis_memory_pressure", "memory_usage_percent", 80),
        }
        if result.tool_name in measurements:
            signal, field, threshold = measurements[result.tool_name]
            value = number(data.get(field))
            if value is not None and 0 <= value < threshold:
                add(result, signal, ObservationQuality.NORMAL,
                    f"direct mechanism measurement {field}={value:g} below its anomaly threshold", healthy=True)
            else:
                # A finite high but stable historical value is not a validated
                # incident change. Let the existing collector provide causality.
                if not any(signal == cause.value for e in source_evidence for cause in e.supports):
                    add(result, signal, ObservationQuality.MISSING_BASELINE if value is not None else ObservationQuality.NO_DATA,
                        "mechanism observation has no validated incident change")
        # Domain and log evidence is useful for existence only when its source
        # and affected service are tied to this alert, never just its label.
        if result.tool_name not in {"metrics.query", "rpc.metrics"}:
            for item in source_evidence:
                if not item.supports:
                    continue
                if item.service not in scope:
                    add(result, "root_support", ObservationQuality.UNSCOPED, "causal evidence outside alert dependency scope", ids=[item.evidence_id], service=item.service)
                    continue
                if (result.tool_name == "db.slowlog" and "database_latency_cohorts" in data
                        and not any(c.get("corroborated") and c.get("service") in scope for c in data["database_latency_cohorts"])):
                    add(result, "db_slow_query", ObservationQuality.UNSCOPED, "slow queries are outside alert dependency scope", ids=[item.evidence_id])
                    continue
                for cause in item.supports:
                    signal = "timeout" if cause == RootCauseType.RPC_TIMEOUT else "error" if cause == RootCauseType.RPC_ERROR_RATE else cause.value
                    if result.tool_name == "traces.query" and signal in {"timeout", "error"}:
                        continue  # Require actual scoped, incident-local failed spans above.
                    if result.tool_name == "traces.query" and signal == "db_slow_query" and not any(
                            c["corroborated"] for c in database_cohorts):
                        add(result, signal, ObservationQuality.NO_DATA, "no corroborated incident-local database cohort", ids=[item.evidence_id])
                        continue
                    if result.tool_name == "logs.query" and not any(
                            isinstance(row, dict) and row.get("service", alert.service_name) in scope
                            and incident_local(row.get("timestamp"), alert)
                            and any(signature in row.get("message", "") for signature in ("OutOfMemory", "OOMKilled"))
                            for row in data.get("logs", data.get("messages", []))):
                        add(result, signal, ObservationQuality.MISSING_BASELINE, "log signature lacks incident-local service and time context")
                        continue
                    add(result, signal, ObservationQuality.ABNORMAL, "specific scoped causal observation", ids=[item.evidence_id], service=item.service)
                    if signal == "db_slow_query":
                        add(result, "latency", ObservationQuality.ABNORMAL, "corroborated scoped slow database operations",
                            ids=[item.evidence_id], service=item.service)
                    elif signal == "oom_restart":
                        add(result, "memory_usage", ObservationQuality.ABNORMAL, "scoped OOM observation",
                            ids=[item.evidence_id], service=item.service)
    return findings, scope


def decide_diagnosis(alert, results, evidence, candidates, gate):
    observations, scope = assess_observations(alert, results, evidence)
    grouped = defaultdict(list)
    for item in observations:
        grouped[item.signal].append(item)
    conflicts = []
    # Only compare identical measurement sources/populations. A healthy
    # downstream call does not contradict an unhealthy incoming request rate.
    populations = defaultdict(set)
    for item in observations:
        populations[(item.tool_name, item.signal, item.service)].add(item.quality)
    for key, qualities in populations.items():
        if ObservationQuality.NORMAL in qualities and ObservationQuality.ABNORMAL in qualities:
            conflicts.append("conflicting measurements for " + "/".join(key))
    measurement_conflicts = list(conflicts)
    top = candidates[0] if candidates else None
    if top:
        conflicts.extend(f"evidence {e.evidence_id} contradicts provisional {top.root_cause_type.value}"
                         for e in evidence if top.root_cause_type in e.contradicts and e.service in scope)
    existence_signals = initial_signals(alert, results)
    required = {signal: "existence" for signal in existence_signals}
    # A close competing mechanism needs a direct domain observation; new
    # evidence can create or discharge this requirement on every round.
    if top and top.root_cause_type != RootCauseType.NO_FAULT:
        for candidate in candidates[:2]:
            if candidate == top or top.confidence - candidate.confidence < 0.15:
                signal = CAUSE_SIGNALS.get(candidate.root_cause_type.value, candidate.root_cause_type.value)
                if signal in SIGNAL_TOOLS:
                    required.setdefault(signal, "root discrimination")
    requirements = []
    for signal, purpose in sorted(required.items()):
        rows = grouped[signal]
        positive = [r for r in rows if r.quality == ObservationQuality.ABNORMAL]
        normal = [r for r in rows if r.quality == ObservationQuality.NORMAL
                  and (r.healthy_reference or purpose == "root discrimination")]
        conflict = any(f"/{signal}/" in message for message in conflicts)
        usable = positive or normal
        quality = ObservationQuality.CONFLICT if conflict else usable[0].quality if usable else rows[0].quality if rows else ObservationQuality.NO_DATA
        requirements.append(EvidenceRequirement(
            requirement_id=f"{alert.service_name}:{signal}:{purpose}", signal=signal, service=alert.service_name,
            purpose=purpose, satisfied=bool(usable) and not conflict, quality=quality,
            observation_ids=[r.observation_id for r in rows], suggested_tools=SIGNAL_TOOLS.get(signal, []),
            reason="scoped valid observation available" if usable and not conflict else "observation missing, ambiguous or insufficient"))
    abnormal = [o for o in observations if o.quality == ObservationQuality.ABNORMAL]
    relevant_abnormal = [o for o in abnormal if o.signal in existence_signals - {"activity"}]
    if alert.alert_type.value == "custom":
        # Broad health/completion alerts can reveal a mechanism not visible in
        # generic request or resource gauges. Existence follows observations,
        # independently of which candidate happens to rank first.
        relevant_abnormal = [o for o in abnormal if o.signal != "bad_deployment"]
    existence = [r for r in requirements if r.purpose == "existence"]
    covered_healthy = bool(existence) and all(r.satisfied and r.quality == ObservationQuality.NORMAL for r in existence)
    presence = FaultPresence.UNKNOWN if measurement_conflicts else FaultPresence.PRESENT if relevant_abnormal else FaultPresence.ABSENT if covered_healthy else FaultPresence.UNKNOWN
    support_ids = {identity for item in abnormal for identity in item.evidence_ids}
    root_supported = bool(top and set(top.evidence_ids) & support_ids and top.root_cause_type != RootCauseType.NO_FAULT)
    validated_sources = {e.source_group for e in evidence if top and e.evidence_id in top.evidence_ids
                         and e.evidence_id in support_ids and top.root_cause_type in e.supports
                         and e.source_group}
    # A passing historical gate may include stale or ambiguous sources. All
    # sources it counted must survive the current observation-quality checks.
    root_supported = root_supported and len(validated_sources) >= gate.independent_source_count
    unscoped_ids = {identity for item in observations if item.quality == ObservationQuality.UNSCOPED for identity in item.evidence_ids}
    scoped_root = not top or not (set(top.evidence_ids) & unscoped_ids)
    discrimination = all(r.satisfied for r in requirements if r.purpose == "root discrimination")
    mechanism = CAUSE_SIGNALS.get(top.root_cause_type.value, top.root_cause_type.value) if top else None
    if mechanism in SIGNAL_TOOLS:
        discrimination = discrimination and any(o.signal == mechanism for o in abnormal)
    confirmed = presence == FaultPresence.PRESENT and gate.sufficient and root_supported and scoped_root and discrimination and not conflicts
    if confirmed:
        verdict, reason = DiagnosisVerdict.CONFIRMED, "scoped fault present; root gate and mechanism requirements satisfied; no unresolved contradiction"
    elif presence == FaultPresence.ABSENT:
        verdict, reason = DiagnosisVerdict.NO_FAULT, "key observations and healthy references support absence within this alert's scope and window"
    else:
        verdict = DiagnosisVerdict.INCONCLUSIVE
        reason = "fault exists but root is not confirmed" if presence == FaultPresence.PRESENT else "fault existence remains unknown"
        if gate.budget_exhausted:
            reason += "; investigation budget exhausted, not evidence sufficiency"
        if conflicts:
            reason += "; unresolved contradictions"
        if not scoped_root:
            reason += "; candidate depends on out-of-scope evidence"
    return DiagnosisDecision(verdict=verdict, fault_presence=presence, reason=reason, requirements=requirements,
                             observations=observations, contradictions=conflicts, root_cause_confirmed=confirmed)

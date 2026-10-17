"""Deterministic, scope-aware fault existence and diagnostic sufficiency.

Ranking and execution success are inputs, never substitutes for observation
coverage. No control metadata, expected answers or LLM decisions are read here.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
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
    "rpc_timeout_rate": ["rpc.metrics"], "rpc_error_rate": ["rpc.metrics"],
    "rpc_latency": ["rpc.metrics"],
    "cpu_usage": ["metrics.query"], "memory_usage": ["metrics.query", "logs.query"],
    "disk_usage": ["metrics.query"], "activity": ["metrics.query", "traces.query", "rpc.metrics"],
    "db_slow_query": ["db.slowlog"], "db_replication_lag": ["db.replication"],
    "db_connection_exhausted": ["db.connections"], "redis_memory_pressure": ["redis.memory"],
    "redis_low_hit_rate": ["redis.hotkeys"], "kafka_consumer_lag": ["kafka.lag"],
    "bad_deployment": ["changes.query"], "oom_restart": ["logs.query"],
    # No current tool measures end-to-end asynchronous completion directly.
    "completion": [],
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

    def add(result, signal, quality, reason, *, healthy=False, ids=None, service=None,
            measurement=None, metric_name=None):
        payload = measurement if isinstance(measurement, dict) else {}
        points = payload.get("data_points", [])
        stamps = [number(point.get("timestamp")) for point in points if isinstance(point, dict)]
        stamps = [stamp for stamp in stamps if stamp is not None]
        start = number(payload.get("window_start"))
        end = number(payload.get("window_end"))
        if start is None and stamps:
            start = min(stamps)
        if end is None and stamps:
            end = max(stamps)
        findings.append(ObservationAssessment(
            observation_id=f"{result.tool_call_id}:{signal}:{len(findings)}", tool_name=result.tool_name,
            signal=signal, service=service or result.target_service or alert.service_name, quality=quality,
            reason=reason, healthy_reference=healthy, evidence_ids=ids or [],
            metric_definition=metric_name,
            unit=str(payload.get("unit", "normalized")) if metric_name else None,
            aggregation=str(payload.get("aggregation_type", "current")) if metric_name else None,
            window_start=start, window_end=end))

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
        declared = data.get("service_name", data.get("service", result.target_service))
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
                    add(result, signal, ObservationQuality.NO_DATA, "current metric is outside the incident window",
                        measurement=payload, metric_name=metric)
                    continue
                if value is None or value < 0:
                    add(result, signal, ObservationQuality.NO_DATA, "non-finite or missing current metric",
                        measurement=payload, metric_name=metric)
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
                    add(result, signal, ObservationQuality.ABNORMAL, "material scoped metric anomaly", ids=ids,
                        measurement=payload, metric_name=metric)
                elif reference(payload, signal, alert):
                    # Recheck the current window; health is not inferred from
                    # the absence of ranker votes or from successful collection.
                    shift = change(payload, minimum_delta=100 if signal == "latency" else 1 if signal == "error" else 0.05,
                                   minimum_ratio=3 if signal == "latency" else 1.5 if signal == "error" else 1.1,
                                   reset_aware=signal.endswith("_usage"))
                    if shift and threshold is None:
                        add(result, signal, ObservationQuality.ABNORMAL, f"observed material change {shift}",
                            measurement=payload, metric_name=metric)
                    else:
                        add(result, signal, ObservationQuality.NORMAL, "stable recent metric with a valid healthy reference",
                            healthy=True, measurement=payload, metric_name=metric)
                else:
                    add(result, signal, ObservationQuality.MISSING_BASELINE, "missing, unstable, insufficient or unhealthy reference",
                        measurement=payload, metric_name=metric)
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
                    if result.tool_name == "rpc.metrics":
                        direct = "rpc_timeout_rate" if signal == "timeout" else "rpc_error_rate"
                        quality = (ObservationQuality.ABNORMAL if rate is not None and 0.05 <= rate <= 1
                                   else ObservationQuality.NORMAL if rate is not None and 0 <= rate < 0.05 and count >= 3
                                   else ObservationQuality.MISSING_BASELINE if rate is not None and 0 <= rate < 0.05
                                   else ObservationQuality.NO_DATA)
                        add(result, direct, quality, "direct protocol outcome measurement",
                            healthy=quality == ObservationQuality.NORMAL, measurement=data, metric_name=field)
                latency = number(data.get("latency_ms"))
                baseline = number(data.get("baseline_latency_ms"))
                if result.tool_name == "rpc.metrics":
                    quality = (ObservationQuality.NO_DATA if latency is None or baseline is None
                               else ObservationQuality.ABNORMAL if latency >= 1000 and latency >= 3 * max(baseline, 1)
                               else ObservationQuality.NORMAL if count >= 3 else ObservationQuality.MISSING_BASELINE)
                    add(result, "rpc_latency", quality, "direct outgoing latency and baseline measurement",
                        healthy=quality == ObservationQuality.NORMAL, measurement=data, metric_name="latency_ms")
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


@dataclass
class DecisionInputs:
    observations: list[ObservationAssessment]
    scope: set[str]
    conflicts: list[str]
    measurement_conflicts: list[str]
    requirements: list[EvidenceRequirement]
    requirements_version: int


def _comparable(left: ObservationAssessment, right: ObservationAssessment) -> bool:
    """Only simultaneous readings of the same defined population can conflict."""
    return (left.tool_name, left.signal, left.service, left.metric_definition,
            left.unit, left.aggregation) == (right.tool_name, right.signal, right.service,
                                             right.metric_definition, right.unit, right.aggregation) and (
        left.metric_definition is not None and left.unit is not None and left.aggregation is not None
        and left.window_start is not None and left.window_end is not None
        and right.window_start is not None and right.window_end is not None
        and max(left.window_start, right.window_start) <= min(left.window_end, right.window_end)
    )


def _observation_triggers(alert, results, observations, scope, evidence):
    """Observed incident-local failures and message activity, without labels or topology guesses."""
    triggered = []
    asynchronous = alert.alert_type.value == "custom" and any(
        word in alert.description.lower() for word in ("completion", "backlog", "queue"))
    for result in results:
        if result.tool_name != "traces.query" or result.status != ToolStatus.SUCCESS:
            continue
        data = (result.data or {}).get("observations", {})
        for trace in data.get("traces", []):
            spans = trace.get("spans", [])
            for span in spans:
                service = span.get("service")
                if service not in scope or not incident_local(span.get("start_time"), alert):
                    continue
                kind = str(span.get("span_kind", "")).lower()
                status = str(span.get("status", "")).upper()
                raw_ref = f"trace:{trace.get('trace_id')}/span:{span.get('span_id')}"
                relevant = [o.observation_id for o in observations if o.tool_name == result.tool_name
                            and o.service == service and str(span.get("span_id")) in o.reason]
                if not relevant:
                    relevant = [o.observation_id for o in observations if o.tool_name == result.tool_name
                                and o.signal == "activity" and o.service == alert.service_name]
                if not relevant:
                    continue
                if kind in {"producer", "consumer"} and asynchronous:
                    triggered.append((alert.service_name, "kafka_consumer_lag", "message chain",
                                      str(span.get("operation") or "messaging"), relevant))
                if kind != "client":
                    continue
                children = [child.get("service") for child in spans
                            if child.get("parent_span_id") == span.get("span_id")
                            and child.get("service") != service]
                dependency = str(span.get("peer_service") or span.get("dependency") or
                                 (children[0] if children else "downstream"))
                if status in {"ERROR", "FAILED", "TIMEOUT", "CANCELLED", "ABORTED"}:
                    signal = "rpc_timeout_rate" if status == "TIMEOUT" else "rpc_error_rate"
                elif number(span.get("duration_ms")) is not None and number(span.get("duration_ms")) > 1000:
                    signal = "db_slow_query" if span.get("db_system") and any(
                        e.raw_ref == raw_ref and e.evidence_type == "trace.db_slow" for e in evidence
                    ) else "rpc_latency" if any(
                        e.raw_ref == raw_ref and e.evidence_type == "trace.span_slow" for e in evidence
                    ) else None
                else:
                    signal = None
                if signal:
                    triggered.append((service, signal, "observed dependency", dependency, relevant))
    return triggered


def prepare_decision(alert, results, evidence, candidates, *, previous_requirements=None,
                     previous_version=0) -> DecisionInputs:
    observations, scope = assess_observations(alert, results, evidence)
    grouped = defaultdict(list)
    for item in observations:
        grouped[(item.service, item.signal)].append(item)
    conflicts = []
    for index, left in enumerate(observations):
        if left.quality not in {ObservationQuality.NORMAL, ObservationQuality.ABNORMAL}:
            continue
        for right in observations[index + 1:]:
            if {left.quality, right.quality} == {ObservationQuality.NORMAL, ObservationQuality.ABNORMAL} and _comparable(left, right):
                conflicts.append(f"conflicting measurements for {left.service}/{left.signal}"
                                 f" in {left.window_start:g}-{left.window_end:g}")
    conflicts = sorted(set(conflicts))
    measurement_conflicts = list(conflicts)
    top = candidates[0] if candidates else None
    if top:
        conflicts.extend(f"evidence {e.evidence_id} contradicts provisional {top.root_cause_type.value}"
                         for e in evidence if top.root_cause_type in e.contradicts and e.service in scope)
    demanded = {}

    def demand(service, signal, purpose, *, dependency=None, triggers=None, blocking=False):
        key = (service, dependency or "", signal, purpose)
        entry = demanded.setdefault(key, {"triggers": set(), "blocking": False})
        entry["triggers"].update(triggers or [])
        entry["blocking"] |= blocking

    for signal in initial_signals(alert, results):
        demand(alert.service_name, signal, "existence")
    if top and top.root_cause_type != RootCauseType.NO_FAULT:
        for candidate in candidates[:2]:
            if candidate == top or top.confidence - candidate.confidence < 0.15:
                signal = CAUSE_SIGNALS.get(candidate.root_cause_type.value, candidate.root_cause_type.value)
                if signal in SIGNAL_TOOLS:
                    demand(alert.service_name, signal, "root discrimination", blocking=candidate == top)
    for service, signal, purpose, dependency, triggers in _observation_triggers(
            alert, results, observations, scope, evidence):
        demand(service, signal, purpose, dependency=dependency, triggers=triggers, blocking=True)

    previous = {r.requirement_id: r for r in previous_requirements or []}
    requirements = []
    for (service, dependency, signal, purpose), metadata in sorted(demanded.items()):
        rows = grouped[(service, signal)]
        positive = [r for r in rows if r.quality == ObservationQuality.ABNORMAL]
        normal = [r for r in rows if r.quality == ObservationQuality.NORMAL
                  and (r.healthy_reference or purpose == "root discrimination")]
        conflict = any(f"{service}/{signal}" in message for message in measurement_conflicts)
        usable = positive or normal
        quality = (ObservationQuality.CONFLICT if conflict else usable[0].quality if usable
                   else rows[0].quality if rows else ObservationQuality.NO_DATA)
        identity = f"{service}:{dependency or '-'}:{signal}:{purpose}"
        requirements.append(EvidenceRequirement(
            requirement_id=identity, signal=signal, service=service, dependency=dependency or None,
            purpose=purpose, satisfied=bool(usable) and not conflict, quality=quality,
            observation_ids=[r.observation_id for r in rows], suggested_tools=SIGNAL_TOOLS.get(signal, []),
            reason="scoped valid observation available" if usable and not conflict
                   else "no capable tool" if not SIGNAL_TOOLS.get(signal)
                   else "observation missing, ambiguous or insufficient",
            trigger_observation_ids=sorted(metadata["triggers"]), blocking=metadata["blocking"]))
    for identity, old in previous.items():
        if identity in {r.requirement_id for r in requirements} or not old.active:
            continue
        trigger_present = bool(set(old.trigger_observation_ids) & {o.observation_id for o in observations})
        if (old.purpose == "root discrimination" or trigger_present) and (not old.satisfied or trigger_present):
            requirements.append(old.model_copy(update={"change_reason": "retained until explicitly resolved"}))
            continue
        requirements.append(old.model_copy(update={"active": False, "blocking": False,
                                           "change_reason": "trigger absent or mechanism checked and no longer relevant"}))
    requirements.sort(key=lambda item: item.requirement_id)
    current_signature = [r.model_dump(exclude={"version", "change_reason"}) for r in requirements]
    previous_signature = [r.model_dump(exclude={"version", "change_reason"}) for r in sorted(
        previous.values(), key=lambda item: item.requirement_id)]
    version = previous_version + 1 if current_signature != previous_signature else max(previous_version, 1)
    for req in requirements:
        old = previous.get(req.requirement_id)
        req.version = version
        if not req.change_reason:
            req.change_reason = ("new observation or hypothesis" if old is None else
                                 "coverage or trigger changed" if req.model_dump(exclude={"version", "change_reason"})
                                 != old.model_dump(exclude={"version", "change_reason"}) else "unchanged")
    return DecisionInputs(observations, scope, conflicts, measurement_conflicts, requirements, version)


def decide_diagnosis(alert, results, evidence, candidates, gate, *, prepared=None):
    managed_requirements = prepared is not None
    prepared = prepared or prepare_decision(alert, results, evidence, candidates)
    observations = prepared.observations
    conflicts, measurement_conflicts = prepared.conflicts, prepared.measurement_conflicts
    requirements = prepared.requirements
    existence_signals = initial_signals(alert, results)
    top = candidates[0] if candidates else None
    abnormal = [o for o in observations if o.quality == ObservationQuality.ABNORMAL]
    relevant_abnormal = [o for o in abnormal if o.signal in existence_signals - {"activity"}]
    if alert.alert_type.value == "custom":
        # Broad health/completion alerts can reveal a mechanism not visible in
        # generic request or resource gauges. Existence follows observations,
        # independently of which candidate happens to rank first.
        relevant_abnormal = [o for o in abnormal if o.signal != "bad_deployment"]
    existence = [r for r in requirements if r.active and r.purpose == "existence"]
    covered_healthy = bool(existence) and all(r.satisfied and r.quality == ObservationQuality.NORMAL for r in existence)
    unresolved = any(r.active and r.blocking and not r.satisfied for r in requirements)
    presence = (FaultPresence.UNKNOWN if measurement_conflicts else FaultPresence.PRESENT if relevant_abnormal
                else FaultPresence.ABSENT if covered_healthy and not unresolved else FaultPresence.UNKNOWN)
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
    discrimination = all(r.satisfied for r in requirements if r.active and r.blocking)
    mechanism = CAUSE_SIGNALS.get(top.root_cause_type.value, top.root_cause_type.value) if top else None
    if mechanism in SIGNAL_TOOLS:
        discrimination = discrimination and any(o.signal == mechanism for o in abnormal)
    direct_checks = {
        RootCauseType.RPC_TIMEOUT: ("rpc_timeout_rate", "rpc.metrics"),
        RootCauseType.RPC_ERROR_RATE: ("rpc_error_rate", "rpc.metrics"),
        RootCauseType.DB_SLOW_QUERY: ("db_slow_query", "db.slowlog"),
        RootCauseType.KAFKA_CONSUMER_LAG: ("kafka_consumer_lag", "kafka.lag"),
    }
    if top and top.root_cause_type in direct_checks:
        signal, tool = direct_checks[top.root_cause_type]
        triggered = [r for r in requirements if r.active and r.blocking and r.signal == signal
                     and r.purpose in {"observed dependency", "message chain"}]
        if triggered:
            discrimination = discrimination and any(
                o.quality == ObservationQuality.ABNORMAL and o.signal == signal
                and o.tool_name == tool and o.service == requirement.service
                for requirement in triggered for o in observations)
    current_gate = not managed_requirements or gate.requirements_version == prepared.requirements_version
    confirmed = (presence == FaultPresence.PRESENT and current_gate and gate.sufficient and root_supported
                 and scoped_root and discrimination and not conflicts)
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
                             observations=observations, contradictions=conflicts, root_cause_confirmed=confirmed,
                             requirements_version=prepared.requirements_version)

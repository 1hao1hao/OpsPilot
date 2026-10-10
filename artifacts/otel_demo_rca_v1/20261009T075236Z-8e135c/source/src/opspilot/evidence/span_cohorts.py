"""Incident-local database latency corroboration, independent of fault labels."""

import math
from collections import defaultdict
from statistics import mean

DATABASE_SYSTEMS = {"postgresql", "postgres", "mysql", "mssql", "oracle"}


def database_latency_cohorts(spans, timestamp):
    """Return slow span IDs supported by a recent group of like database calls.

    One slow query is useful drill-down context, but is insufficient to label
    its entire dependency as the incident cause. Use the same one-second
    latency budget as span anomaly detection, applied to the group's mean,
    with at least three slow calls. Timestamp-less legacy snapshots retain
    their existing semantics; live spans always carry timestamps.
    """
    groups = defaultdict(dict)
    legacy = set()
    for identity, span in spans.items():
        if span.get("db_system") not in DATABASE_SYSTEMS:
            continue
        if "start_time" not in span:
            legacy.add(identity)
            continue
        duration = float(span.get("duration_ms", 0))
        started = float(span["start_time"])
        if not math.isfinite(duration) or not math.isfinite(started) or duration < 0:
            continue
        # Do not let a whole Jaeger tree drag older dependency calls into
        # the current incident. Include calls in progress at the alert.
        if started < timestamp - 60 or started > timestamp + 60:
            continue
        key = (span.get("service"), span.get("db_system"), span.get("operation"))
        groups[key][identity] = span
    supported = set(legacy)
    cohorts = []
    for key, calls in groups.items():
        slow = {identity for identity, s in calls.items() if s["duration_ms"] > 1000}
        average = mean(s["duration_ms"] for s in calls.values())
        corroborated = len(slow) >= 3 and average > 1000
        if corroborated:
            supported.update(slow)
        cohorts.append({"service": key[0], "db_system": key[1], "operation": key[2],
                        "sample_count": len(calls), "slow_count": len(slow),
                        "mean_duration_ms": average, "corroborated": corroborated})
    return supported, cohorts
